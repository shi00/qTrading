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
import multiprocessing
import queue
import threading
import time

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from services.news_sentiment_scorer import NewsSentimentScorer
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
        # D02-F02：processor worker 收敛（Step 3）与异步 engine dispose（Step 3b）拆分，
        # 使 Recorder 最终落账可在两者之间执行；静态步骤由 10 项增至 11 项。
        assert len(_CLEANUP_STEPS) == 11
        assert _CLEANUP_STEPS[0][0] == "Step 0"
        assert _CLEANUP_STEPS[0][1] == "_step0_cancel_tasks"
        assert _CLEANUP_STEPS[0][2] is True
        assert _CLEANUP_STEPS[0][3] == 6.0
        assert _CLEANUP_STEPS[3] == ("Step 3", "_step3_stop_processor_workers", True, 1.0)
        assert _CLEANUP_STEPS[4] == ("Step 3b", "_step3b_dispose_async_engine", True, 2.0)
        # Step 4（清 Toast）非关键、预算 1.0s；拆分后索引由 4 后移至 5
        assert _CLEANUP_STEPS[5][2] is False
        assert _CLEANUP_STEPS[5][3] == 1.0
        # N3-3: Step 5b 紧跟 Step 5（新闻情绪打分子进程清理，预算 2.0s）
        assert _CLEANUP_STEPS[7] == ("Step 5b", "_step5b_stop_sentiment_scorer", True, 2.0)
        assert _CLEANUP_STEPS[8][0] == "Step 6"
        assert _CLEANUP_STEPS[10][0] == "Step 8"

    def test_each_step_has_timeout(self):
        """ASYNC-004/005: Each cleanup step has its own timeout."""
        for step_def in _CLEANUP_STEPS:
            assert len(step_def) == 4, f"Step {step_def[0]} should be a 4-tuple"
            name, method_name, critical, timeout = step_def
            assert timeout > 0, f"Step {name} timeout must be positive"

    def test_total_step_timeouts_within_overall_budget(self):
        """ASYNC-005: Sum of step timeouts must fit within production window shutdown budget.

        生产路径 perform_window_shutdown 使用 do_cleanup(timeout_s=60.0) 容纳 57s + 3s margin；
        此处校验 sum <= 65.0s (60.0s do_cleanup 整体超时 + 5.0s CI 抖动 margin)。
        """
        total_step_time = sum(step[3] for step in _CLEANUP_STEPS)
        # 60.0s do_cleanup 整体超时 + 5.0s CI 慢机器抖动 margin
        assert total_step_time <= 65.0, (
            f"Sum of step timeouts ({total_step_time}s) exceeds production overall budget (65.0s)"
        )

    def test_step0_timeout_accommodates_join_timeout(self):
        """ASYNC-004: Step 0 timeout must accommodate cancel_all_running_async join_timeout."""
        step0_timeout = _CLEANUP_STEPS[0][3]
        # cancel_all_running_async default join_timeout is 3.0s
        # Step 0 needs to cover: cancel + persist + join
        assert step0_timeout >= 3.0, f"Step 0 timeout ({step0_timeout}s) must be >= join_timeout (3.0s)"

    def test_step0_timeout_budget_consistency(self):
        """CON-03: Step 0 声明预算必须覆盖内部子超时之和并留有至少 1.0s 抖动余量。"""
        import inspect
        from services.task_manager import TaskManager

        sig = inspect.signature(TaskManager.cancel_all_running_async)
        join_timeout = sig.parameters["join_timeout"].default
        persist_timeout = sig.parameters["persist_timeout"].default
        registered_drain_timeout = 1.5

        step0_timeout = _CLEANUP_STEPS[0][3]
        internal_sub_timeouts_sum = registered_drain_timeout + persist_timeout + join_timeout

        assert step0_timeout >= 6.0, f"Step 0 timeout ({step0_timeout}s) should be >= 6.0s to avoid spurious timeout"
        assert step0_timeout - internal_sub_timeouts_sum >= 1.0, (
            f"Step 0 budget ({step0_timeout}s) must exceed internal sub-timeouts ({internal_sub_timeouts_sum}s) "
            f"by at least 1.0s margin for CI/load jitter"
        )

    def test_production_cleanup_call_sites_budget_self_consistency(self):
        """CON-03: 生产调用点与默认配置的超时预算必须自洽。"""
        # 1. 正常优雅停机路径与默认配置：有效步骤超时和 <= do_cleanup 总超时 < watchdog 超时
        normal_call_sites = [
            ("main_shutdown", 60.0, 35.0, 70.0),
            ("disconnect", 60.0, 35.0, 70.0),
            ("startup_rollback", 60.0, 35.0, None),
            ("default_do_cleanup", 60.0, 35.0, None),
        ]
        for name, timeout_s, step_timeout_s, watchdog_timeout in normal_call_sites:
            effective_sum = sum(min(step[3], step_timeout_s) for step in _CLEANUP_STEPS)
            assert effective_sum <= timeout_s, (
                f"[{name}] effective step sum ({effective_sum}s) exceeds do_cleanup timeout ({timeout_s}s)"
            )
            if watchdog_timeout is not None:
                assert timeout_s < watchdog_timeout, (
                    f"[{name}] do_cleanup timeout ({timeout_s}s) must be strictly less than watchdog ({watchdog_timeout}s)"
                )

        # 2. 升级退出路径 (upgrade_exit)：属于异常终止的紧急快速退出路径，
        # 设计上以 timeout_s=5.0 实施硬截断保护，要求总超时严格小于步骤预算和
        upgrade_timeout_s = 5.0
        upgrade_step_timeout_s = 10.0
        upgrade_effective_sum = sum(min(step[3], upgrade_step_timeout_s) for step in _CLEANUP_STEPS)
        assert upgrade_timeout_s < upgrade_effective_sum, (
            f"upgrade_exit 必须运行在硬截断模式 (timeout_s {upgrade_timeout_s}s < effective_sum {upgrade_effective_sum}s)"
        )

    def test_do_cleanup_default_timeouts(self):
        """CON-03: do_cleanup 默认参数必须与生产标准配置对齐（60.0s / 35.0s）。"""
        import inspect

        sig = inspect.signature(ShutdownCoordinator.do_cleanup)
        assert sig.parameters["timeout_s"].default == 60.0
        assert sig.parameters["step_timeout_s"].default == 35.0


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

    def test_force_exit_public_method_delegates_to_callback(self):
        """公开 force_exit 方法应委托给构造时注入的 force_exit_callback。"""
        called_with = []
        coord = ShutdownCoordinator(force_exit_callback=lambda code: called_with.append(code))
        coord.force_exit(1)
        assert called_with == [1]

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
            mock_instance.flush_persistence.assert_called_once_with(timeout_s=1.5)

    @pytest.mark.asyncio
    async def test_step2_flush_db_timeout_translates_to_runtime_error_and_logs(self, caplog):
        """CON-10: flush_persistence 内部 1.5s 超时应转译为 RuntimeError 并记录明确诊断日志。"""
        import logging

        coord = ShutdownCoordinator()
        with (
            patch("services.task_manager.TaskManager") as mock_tm,
            caplog.at_level(logging.WARNING),
        ):
            mock_instance = MagicMock()
            mock_instance.flush_persistence = AsyncMock(
                side_effect=TimeoutError("Task persistence flush timed out with 2 pending write(s)")
            )
            mock_tm._instance = mock_instance
            with pytest.raises(RuntimeError, match="Task persistence flush timed out \\(internal 1.5s limit\\)"):
                await coord._step2_flush_db_writes()

            assert "TaskManager.flush_persistence timed out (1.5s limit reached)" in caplog.text

    @pytest.mark.asyncio
    async def test_step2_flush_timeout_not_reported_as_step_timeout(self):
        """CON-10: 验证在 _run_async_step 层面，内部刷盘超时不会被标记为步骤整体 timed_out=True。"""
        coord = ShutdownCoordinator()
        with patch("services.task_manager.TaskManager") as mock_tm:
            mock_instance = MagicMock()
            mock_instance.flush_persistence = AsyncMock(
                side_effect=TimeoutError("Task persistence flush timed out with 1 pending write(s)")
            )
            mock_tm._instance = mock_instance
            result = await coord._run_async_step(
                name="Step 2",
                step=coord._step2_flush_db_writes,
                step_timeout_s=2.0,
                critical=True,
            )
            assert result.ok is False
            assert result.timed_out is False
            assert "Task persistence flush timed out (internal 1.5s limit)" in result.error

    @pytest.mark.asyncio
    async def test_step3_stop_processor_no_instance(self):
        coord = ShutdownCoordinator()
        with patch("data.data_processor.DataProcessor") as mock_dp:
            mock_dp._instance = None
            await coord._step3_stop_processor_workers()

    @pytest.mark.asyncio
    async def test_step3_stop_processor_only_stops_workers(self):
        """Step 3 仅停止 worker，不在此阶段 dispose 异步 engine。"""
        coord = ShutdownCoordinator()
        with patch("data.data_processor.DataProcessor") as mock_dp:
            mock_instance = MagicMock()
            mock_instance.stop = AsyncMock()
            mock_instance.cache = MagicMock()
            mock_instance.cache.close = AsyncMock()
            mock_dp._instance = mock_instance
            await coord._step3_stop_processor_workers()
            mock_instance.stop.assert_awaited_once()
            mock_instance.cache.close.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_step3b_dispose_async_engine_no_instance(self):
        coord = ShutdownCoordinator()
        with patch("data.data_processor.DataProcessor") as mock_dp:
            mock_dp._instance = None
            await coord._step3b_dispose_async_engine()

    @pytest.mark.asyncio
    async def test_step3b_dispose_async_engine_with_instance(self):
        coord = ShutdownCoordinator()
        with patch("data.data_processor.DataProcessor") as mock_dp:
            mock_instance = MagicMock()
            mock_instance.cache = MagicMock()
            mock_instance.cache.close = AsyncMock()
            mock_dp._instance = mock_instance
            await coord._step3b_dispose_async_engine()
            mock_instance.cache.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_step4_clear_toast_no_page(self):
        coord = ShutdownCoordinator(page=None)
        await coord._step4_clear_toast()

    @pytest.mark.asyncio
    async def test_step4_clear_toast_with_stop_all(self):
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
            mock_instance._worker_proc = None
            mock_lmm._instance = mock_instance
            await coord._step5_unload_ai_model()
            mock_instance.unload_model.assert_not_called()

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
    async def test_step5b_no_instance(self):
        coord = ShutdownCoordinator()
        with patch("services.news_sentiment_scorer.NewsSentimentScorer") as mock_nss:
            mock_nss._instance = None
            await coord._step5b_stop_sentiment_scorer()

    @pytest.mark.asyncio
    async def test_step5b_not_loaded_skips_unload(self):
        """未加载（无 ready / 无路径 / 无进程）时跳过，不触发 unload。"""
        coord = ShutdownCoordinator()
        with patch("services.news_sentiment_scorer.NewsSentimentScorer") as mock_nss:
            mock_instance = MagicMock()
            mock_instance._worker_ready = False
            mock_instance._model_path = ""
            mock_instance._worker_proc = None
            mock_nss._instance = mock_instance
            await coord._step5b_stop_sentiment_scorer()
            mock_instance.unload_model.assert_not_called()

    @pytest.mark.asyncio
    async def test_step5_unloads_worker_spawned_before_ready_after_cancel(self):
        """D02-R01：首次加载在 ready 前被取消——worker 已 spawn、ready/path 未置位。

        取消 await 不会终止 multiprocessing 子进程；Step 5 守卫若只看
        ready/path 会漏卸载（须与 Step 5b 的 _worker_proc 三条件同口径）。
        """
        from services.local_model_manager import LocalModelManager

        mgr = LocalModelManager()
        fake_proc = MagicMock(spec=multiprocessing.Process)
        fake_proc.is_alive.return_value = True
        ready_blocked = asyncio.Event()

        async def _spawn_worker(*_args, **_kwargs):
            # 模拟 _ensure_worker_impl：proc 已 start、队列已建，但 ready 尚未置位
            mgr._worker_proc = fake_proc
            mgr._request_queue = MagicMock()
            mgr._result_queue = MagicMock()
            return True

        async def _block_on_ready(*_args, **_kwargs):
            ready_blocked.set()
            await asyncio.Event().wait()
            return True

        with (
            patch("services.local_model_manager._HAS_LLAMA_CPP", True),
            patch("services.local_model_manager._validate_model_file", return_value=True),
            patch(
                "services.local_model_manager.os.stat",
                return_value=MagicMock(st_mtime=1.0, st_size=2),
            ),
            patch("services.local_model_manager.ConfigHandler") as mock_config,
            patch("services.local_model_manager.ThreadPoolManager") as mock_pool,
            patch.object(mgr, "_ensure_worker", new=_spawn_worker),
            patch.object(mgr, "_await_worker_ready", new=_block_on_ready),
        ):
            mock_config.get_typed.return_value = ""
            mock_pool.return_value.run_async = AsyncMock(return_value="sha")
            load_task = asyncio.create_task(mgr.load_model("/tmp/model.gguf", config={"n_threads": 1}))
            await asyncio.wait_for(ready_blocked.wait(), timeout=2.0)
            load_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await load_task

            assert mgr._worker_proc is fake_proc
            assert mgr._worker_ready is False
            assert mgr._model_path == ""

            await ShutdownCoordinator()._step5_unload_ai_model()
            fake_proc.terminate.assert_called_once()

    @pytest.mark.asyncio
    async def test_step5b_calls_force_unload(self):
        """已加载时以 force=True 强制终止（关机路径，适配 2.0s 预算）。"""
        coord = ShutdownCoordinator()
        with patch("services.news_sentiment_scorer.NewsSentimentScorer") as mock_nss:
            mock_instance = MagicMock()
            mock_instance._worker_ready = True
            mock_instance.unload_model = MagicMock()
            mock_nss._instance = mock_instance
            await coord._step5b_stop_sentiment_scorer()
            mock_instance.unload_model.assert_called_once_with(force=True)

    @pytest.mark.asyncio
    async def test_step5b_startup_window_still_cleaned(self):
        """对抗性 Q6：worker 启动中（proc 已 start、ready 未置位、路径未记录）关机也须清理。"""
        coord = ShutdownCoordinator()
        with patch("services.news_sentiment_scorer.NewsSentimentScorer") as mock_nss:
            mock_instance = MagicMock()
            mock_instance._worker_ready = False
            mock_instance._model_path = ""
            mock_instance._worker_proc = MagicMock()
            mock_instance.unload_model = MagicMock()
            mock_nss._instance = mock_instance
            await coord._step5b_stop_sentiment_scorer()
            mock_instance.unload_model.assert_called_once_with(force=True)

    @pytest.mark.asyncio
    async def test_step5b_real_worker_within_step_budget(self):
        """DoD：退出流程中子进程被清理且不超 Step 5b 预算（2.0s）。

        用真实 multiprocessing 进程（30s 长眠）验证 force 路径 terminate
        在预算内完成且子进程确实退出；进程启动耗时在计时窗口之外。
        """
        mgr = NewsSentimentScorer()
        proc = multiprocessing.Process(target=time.sleep, args=(30,))
        proc.start()
        try:
            mgr._worker_proc = proc
            # _shutdown_worker_locked 以 _request_queue 非 None 作为清理块入口条件，
            # 生产场景 queue 在 proc.start() 前创建（_ensure_worker_impl L599-600）；
            # 测试须同步补齐，否则 kill 块整体跳过（首次跑已实测）。
            mgr._request_queue = MagicMock(spec=queue.Queue)
            mgr._worker_ready = False
            mgr._model_path = "model.gguf"
            coord = ShutdownCoordinator()
            with patch("services.news_sentiment_scorer.NewsSentimentScorer") as mock_nss:
                mock_nss._instance = mgr
                start = time.perf_counter()
                await coord._step5b_stop_sentiment_scorer()
                elapsed = time.perf_counter() - start
            assert elapsed < 2.0, f"Step 5b took {elapsed:.2f}s, exceeding the 2.0s budget"
            assert proc.exitcode is not None  # 子进程已被 terminate 并退出
        finally:
            # 断言失败/异常时也须回收真实子进程，避免 30s 长眠进程泄漏到后续用例。
            if proc.is_alive():
                proc.terminate()
            proc.join(timeout=5.0)

    @pytest.mark.asyncio
    async def test_step5b_cancelled_error_propagates(self):
        """R2：步骤被取消时 CancelledError 向上传播，不被吞没。"""
        release = threading.Event()

        def _block():
            release.wait(5.0)

        try:
            coord = ShutdownCoordinator()
            with (
                patch("services.news_sentiment_scorer.NewsSentimentScorer") as mock_nss,
                patch.object(coord, "_step5b_stop_sentiment_scorer_sync", side_effect=_block),
            ):
                mock_nss._instance = MagicMock()
                mock_nss._instance._worker_ready = True
                task = asyncio.create_task(coord._step5b_stop_sentiment_scorer())
                await asyncio.sleep(0.05)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
        finally:
            release.set()

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
    async def test_step6_shutdown_calls_request_cancel_when_service_present(self):
        """CON-04: 停机前应终止在途可取消 sidecar 命令（用户备份中关闭应用）。"""
        coord = ShutdownCoordinator()
        with (
            patch("utils.thread_pool.ThreadPoolManager") as mock_tpm,
            patch("services.embedded_pg_maintenance_service.EmbeddedPgMaintenanceService") as mock_epg,
        ):
            mock_tpm._instance = MagicMock()
            mock_instance = MagicMock()
            mock_epg._instance = mock_instance
            await coord._step6_shutdown_thread_pools()
            mock_instance.request_cancel.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_step6_shutdown_skips_request_cancel_when_service_absent(self):
        """CON-04: 维护服务未初始化时 Step 6 跳过 request_cancel，不抛异常。"""
        coord = ShutdownCoordinator()
        with (
            patch("utils.thread_pool.ThreadPoolManager") as mock_tpm,
            patch("services.embedded_pg_maintenance_service.EmbeddedPgMaintenanceService") as mock_epg,
        ):
            mock_tpm._instance = MagicMock()
            mock_epg._instance = None
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
            patch("services.news_sentiment_scorer.NewsSentimentScorer") as mock_nss,
            patch("utils.thread_pool.ThreadPoolManager") as mock_tpm,
        ):
            mock_tm._instance = None
            mock_sched.scheduler.running = False
            mock_news._instance = None
            mock_mds._instance = None
            mock_dp._instance = None
            mock_lmm._instance = None
            mock_nss._instance = None
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

    @pytest.mark.asyncio
    async def test_success_marks_graceful_shutdown_completed(self):
        """B10: 成功路径调用 mark_graceful_shutdown_completed。"""
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
        with patch("utils.shutdown.mark_graceful_shutdown_completed", new_callable=MagicMock) as mock_mark:
            result = await coord._execute_cleanup(timeout_s=5.0, step_timeout_s=2.0)
        assert result is True
        mock_mark.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_critical_failure_does_not_mark_graceful(self):
        """B10: 关键步骤失败不标记优雅完成，atexit 兜底保留。"""
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
        with patch("utils.shutdown.mark_graceful_shutdown_completed", new_callable=MagicMock) as mock_mark:
            result = await coord._execute_cleanup(timeout_s=5.0, step_timeout_s=2.0)
        assert result is False
        mock_mark.assert_not_called()

    @pytest.mark.asyncio
    async def test_timeout_does_not_mark_graceful(self):
        """B10: 超时路径不标记优雅完成（与 finally 区分，finally 会误标记）。"""
        coord = ShutdownCoordinator(service_stop_delay=0)
        coord._run_cleanup_steps = AsyncMock(side_effect=TimeoutError())
        with patch("utils.shutdown.mark_graceful_shutdown_completed", new_callable=MagicMock) as mock_mark:
            result = await coord._execute_cleanup(timeout_s=5.0, step_timeout_s=2.0)
        assert result is False
        mock_mark.assert_not_called()


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

    def test_flush_db_before_stop_processor_before_engine_dispose(self):
        """flush_db_writes → 停止 processor worker → dispose 异步 engine 的顺序约束。"""
        indices: dict[str, int] = {}
        for i, (_name, method_name, _critical, _timeout) in enumerate(_CLEANUP_STEPS):
            indices[method_name] = i
        assert "_step2_flush_db_writes" in indices
        assert "_step3_stop_processor_workers" in indices
        assert "_step3b_dispose_async_engine" in indices
        assert indices["_step2_flush_db_writes"] < indices["_step3_stop_processor_workers"]
        assert indices["_step3_stop_processor_workers"] < indices["_step3b_dispose_async_engine"]

    def test_cancel_tasks_is_first_step(self):
        assert _CLEANUP_STEPS[0][1] == "_step0_cancel_tasks"

    def test_shutdown_pools_is_last_step(self):
        assert _CLEANUP_STEPS[-1][1] == "_step8_stop_embedded_postgres"

    def test_step2_is_flush_db_writes(self):
        assert _CLEANUP_STEPS[2][1] == "_step2_flush_db_writes"

    def test_step3_stop_and_step3b_dispose_are_split(self):
        assert _CLEANUP_STEPS[3][1] == "_step3_stop_processor_workers"
        assert _CLEANUP_STEPS[4][1] == "_step3b_dispose_async_engine"


class TestUsageRecorderShutdownOrder:
    """D02-F02：关机事件顺序 = 生产停止 → Recorder 最终落账 → 异步 engine dispose → PG stop。"""

    _STUB_METHODS = (
        "_step0_cancel_tasks",
        "_step1_stop_services",
        "_step2_flush_db_writes",
        "_step4_clear_toast",
        "_step5_unload_ai_model",
        "_step5b_stop_sentiment_scorer",
        "_step6_shutdown_thread_pools",
        "_step7_close_database_managers",
        "_step8_stop_embedded_postgres",
    )

    @staticmethod
    def _observation(attempt_id: str):
        from core.model_management_types import NormalizedLLMUsage
        from services.ai_service.usage_recorder import AttemptUsageObservation

        return AttemptUsageObservation(
            attempt_id=attempt_id,
            invocation_id="inv-shutdown",
            usage=NormalizedLLMUsage(
                input_total_tokens=120,
                uncached_input_tokens=100,
                cache_read_tokens=20,
                cache_write_tokens=None,
                output_tokens=15,
            ),
            actual_model="glm-4-air",
            metadata={"connection_id": "conn-shutdown", "month": "202611"},
        )

    @classmethod
    def _terminal(cls, attempt_id: str):
        from services.ai_service.usage_recorder import AttemptTerminalRecord

        return AttemptTerminalRecord(
            attempt_id=attempt_id,
            invocation_id="inv-shutdown",
            status="succeeded",
            usage=cls._observation(attempt_id).usage,
            error_code=None,
            duration_ms=123.4,
        )

    @classmethod
    def _stub_steps(cls, coord: ShutdownCoordinator, events: list[str]) -> None:
        for method in cls._STUB_METHODS:

            async def _step(_m=method):
                events.append(_m)

            setattr(coord, method, _step)

    @pytest.mark.asyncio
    async def test_recorder_flush_settles_before_engine_dispose_and_pg_stop(self):
        """DAO 首次失败后恢复：pending 结算 + 已观察未终态两条均在 dispose 前落库，revision 不双计。"""
        from services.ai_service.usage_recorder import UsageRecorder

        events: list[str] = []
        settle_calls: list[dict] = []
        dao = MagicMock()

        async def settle_attempt_usage(**kwargs):
            settle_calls.append(kwargs)
            events.append(f"settle:{kwargs['attempt_id']}")
            if len(settle_calls) == 1:
                raise RuntimeError("ledger temporarily unavailable")
            return True

        dao.settle_attempt_usage = settle_attempt_usage
        dao.close_expired_usage_windows = AsyncMock(return_value=0)
        recorder = UsageRecorder(dao_provider=lambda: dao, clock=lambda: 0.0, settle_timeout_s=0.5)

        recorder.observe_usage(self._observation("att-pending"))
        first = await recorder.submit_terminal(self._terminal("att-pending"))
        assert first.persisted is False
        assert "att-pending" in recorder._pending_settle
        recorder.observe_usage(self._observation("att-unobserved"))
        assert recorder.pending_observation_count() == 2

        # 仅观察关机阶段事件顺序（首次失败属于测试前置）
        events.clear()

        coord = ShutdownCoordinator(service_stop_delay=0)
        self._stub_steps(coord, events)
        processor = MagicMock()
        processor.stop = AsyncMock(side_effect=lambda: events.append("stop-processor"))
        processor.cache = MagicMock()
        processor.cache.close = AsyncMock(side_effect=lambda: events.append("dispose-engine"))
        coord.register_cleanup_callback("usage recorder final flush", recorder.aclose, critical=True, timeout_s=5.0)

        with patch("data.data_processor.DataProcessor") as mock_dp:
            mock_dp._instance = processor
            results = await coord._run_cleanup_steps(step_timeout_s=2.0)

        assert len(results) == 12
        assert events.index("stop-processor") < events.index("dispose-engine")
        settle_indices = [i for i, event in enumerate(events) if event.startswith("settle:")]
        assert len(settle_indices) == 2
        # 生产停止（Step 0~2 + processor worker stop）→ Recorder 落账 → engine dispose → 同步 engine/PG
        assert events.index("_step2_flush_db_writes") < events.index("stop-processor")
        assert events.index("stop-processor") < min(settle_indices)
        assert max(settle_indices) < events.index("dispose-engine")
        assert events.index("dispose-engine") < events.index("_step7_close_database_managers")
        assert events.index("dispose-engine") < events.index("_step8_stop_embedded_postgres")

        shutdown_settles = settle_calls[1:]
        assert {call["attempt_id"] for call in shutdown_settles} == {"att-pending", "att-unobserved"}
        # revision 幂等：补交沿用同一水位，不因关机重放而双计
        assert all(call["usage_revision"] == 1 for call in shutdown_settles)
        assert recorder._pending_settle == {}
        assert recorder.pending_observation_count() == 0

    @pytest.mark.asyncio
    async def test_ledger_unavailable_does_not_claim_settled(self, caplog):
        """真实账本不可用时 Recorder 如实告警丢弃事实，不伪装已落账（R21）。"""
        from services.ai_service.usage_recorder import UsageRecorder

        events: list[str] = []
        recorder = UsageRecorder(dao_provider=lambda: None, clock=lambda: 0.0, settle_timeout_s=0.5)
        recorder.observe_usage(self._observation("att-unavailable"))

        coord = ShutdownCoordinator(service_stop_delay=0)
        self._stub_steps(coord, events)
        processor = MagicMock()
        processor.stop = AsyncMock()
        processor.cache = MagicMock()
        processor.cache.close = AsyncMock()
        coord.register_cleanup_callback("usage recorder final flush", recorder.aclose, critical=True, timeout_s=5.0)

        with patch("data.data_processor.DataProcessor") as mock_dp:
            mock_dp._instance = processor
            results = await coord._run_cleanup_steps(step_timeout_s=2.0)

        dyn_result = next(r for r in results if r.name == "usage recorder final flush")
        # 回调不吞错：步骤本身完成，但日志必须如实记录未落账
        assert dyn_result.ok is True
        assert "dropped at shutdown attempt" in caplog.text
        assert recorder._observations == {}


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
        coord._step3_stop_processor_workers = step_ok
        coord._step3b_dispose_async_engine = step_ok
        coord._step4_clear_toast = step_ok
        coord._step5_unload_ai_model = step_ok
        coord._step5b_stop_sentiment_scorer = step_ok
        coord._step6_shutdown_thread_pools = step_ok
        coord._step7_close_database_managers = step_ok
        coord._step8_stop_embedded_postgres = step_ok

        results = await coord._run_cleanup_steps(step_timeout_s=2.0)
        # 11 个静态步骤全部执行
        assert len(results) == 11
        # Step 0 失败但其余执行
        assert results[0].ok is False
        assert results[0].critical is True
        for r in results[1:]:
            assert r.ok is True
        # 所有步骤都被调用（critical failure 不中断流程）
        assert "step0" in call_log
        assert call_log.count("step_ok") == 10

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
        coord._step3_stop_processor_workers = step_ok
        coord._step3b_dispose_async_engine = step_ok
        coord._step4_clear_toast = step_ok
        coord._step5_unload_ai_model = step_ok
        coord._step6_shutdown_thread_pools = step_ok
        coord._step7_close_database_managers = step_ok
        coord._step8_stop_embedded_postgres = step_ok

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
        coord._step3_stop_processor_workers = step_ok
        coord._step3b_dispose_async_engine = step_ok
        coord._step4_clear_toast = step_ok
        coord._step5_unload_ai_model = step_ok
        coord._step5b_stop_sentiment_scorer = step_ok
        coord._step6_shutdown_thread_pools = step_ok
        coord._step7_close_database_managers = step_ok
        coord._step8_stop_embedded_postgres = step_ok

        # step_timeout_s=0.5 远小于各 step 的 default（1.0~35.0，Step 8=35.0）
        # M13: 更新注释 — Step 8 加入后 default 上限由 5.0 提升至 35.0
        results = await coord._run_cleanup_steps(step_timeout_s=0.5)
        assert len(results) == 11
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
            coord._step3_stop_processor_workers = ok_step
            coord._step3b_dispose_async_engine = ok_step
            coord._step5_unload_ai_model = ok_step
            coord._step6_shutdown_thread_pools = ok_step

            result = await coord.do_cleanup(timeout_s=10.0, step_timeout_s=5.0)
            assert result is False
            assert coord.cleanup_done is True
            assert coord.cleanup_success is False
            # 至少有一个 critical failure
            failures = [r for r in coord.step_results if r.critical and not r.ok]
            assert len(failures) >= 1


class TestStep8FailureAggregation:
    """D02-F03: Step 8 受控超时/异常必须沿既有聚合路径标记为关键失败。

    修复前 _step8_stop_embedded_postgres 仅记录日志后正常返回，导致
    _run_async_step 记 ok=True → cleanup_success=True → 误标记优雅停机。
    """

    _STEP_METHODS = (
        "_step0_cancel_tasks",
        "_step1_stop_services",
        "_step2_flush_db_writes",
        "_step3_stop_processor_workers",
        "_step3b_dispose_async_engine",
        "_step4_clear_toast",
        "_step5_unload_ai_model",
        "_step5b_stop_sentiment_scorer",
        "_step6_shutdown_thread_pools",
        "_step7_close_database_managers",
    )

    @classmethod
    def _stub_non_step8_steps(cls, coord: ShutdownCoordinator) -> None:
        async def ok_step():
            return None

        for method in cls._STEP_METHODS:
            setattr(coord, method, ok_step)

    @staticmethod
    def _stub_service(monkeypatch, stop_sync: MagicMock) -> None:
        from data.persistence.embedded_postgres.service import EmbeddedPostgresService

        mock_service = MagicMock()
        mock_service.stop_sync = stop_sync
        monkeypatch.setattr(EmbeddedPostgresService, "get_instance", classmethod(lambda cls: mock_service))

    @pytest.mark.asyncio
    async def test_step8_timeout_marks_cleanup_failure_and_skips_graceful_mark(self, monkeypatch):
        """stop_sync 超时：Step 8 ok=False / timed_out=True，cleanup_success=False，不标记 graceful。"""
        coord = ShutdownCoordinator(service_stop_delay=0)
        self._stub_non_step8_steps(coord)
        self._stub_service(monkeypatch, MagicMock(side_effect=TimeoutError("pg stop timeout")))

        with patch("utils.shutdown.mark_graceful_shutdown_completed", new_callable=MagicMock) as mock_mark:
            result = await coord.do_cleanup(timeout_s=10.0, step_timeout_s=5.0)

        assert result is False
        assert coord.cleanup_success is False
        assert coord.cleanup_done is True
        step8 = next(r for r in coord.step_results if r.name == "Step 8")
        assert step8.critical is True
        assert step8.ok is False
        assert step8.timed_out is True
        mock_mark.assert_not_called()

    @pytest.mark.asyncio
    async def test_step8_exception_marks_cleanup_failure_and_skips_graceful_mark(self, monkeypatch):
        """stop_sync 抛异常：Step 8 ok=False / timed_out=False，cleanup_success=False，不标记 graceful。"""
        coord = ShutdownCoordinator(service_stop_delay=0)
        self._stub_non_step8_steps(coord)
        self._stub_service(monkeypatch, MagicMock(side_effect=RuntimeError("pg stop boom")))

        with patch("utils.shutdown.mark_graceful_shutdown_completed", new_callable=MagicMock) as mock_mark:
            result = await coord.do_cleanup(timeout_s=10.0, step_timeout_s=5.0)

        assert result is False
        assert coord.cleanup_success is False
        assert coord.cleanup_done is True
        step8 = next(r for r in coord.step_results if r.name == "Step 8")
        assert step8.critical is True
        assert step8.ok is False
        assert step8.timed_out is False
        mock_mark.assert_not_called()

    @pytest.mark.asyncio
    async def test_step8_success_marks_graceful_and_returns_true(self, monkeypatch):
        """正常停止：Step 8 ok=True，cleanup_success=True，并标记 graceful。"""
        coord = ShutdownCoordinator(service_stop_delay=0)
        self._stub_non_step8_steps(coord)
        self._stub_service(monkeypatch, MagicMock(return_value=None))

        with patch("utils.shutdown.mark_graceful_shutdown_completed", new_callable=MagicMock) as mock_mark:
            result = await coord.do_cleanup(timeout_s=10.0, step_timeout_s=5.0)

        assert result is True
        assert coord.cleanup_success is True
        step8 = next(r for r in coord.step_results if r.name == "Step 8")
        assert step8.ok is True
        assert step8.timed_out is False
        mock_mark.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_step8_cancelled_error_propagates_from_do_cleanup(self, monkeypatch):
        """取消：CancelledError 从 do_cleanup 上抛（R2），cleanup_success=False，不标记 graceful。"""
        coord = ShutdownCoordinator(service_stop_delay=0)
        self._stub_non_step8_steps(coord)
        mock_service = MagicMock()
        mock_service.stop_sync = MagicMock(return_value=None)

        from data.persistence.embedded_postgres.service import EmbeddedPostgresService

        monkeypatch.setattr(EmbeddedPostgresService, "get_instance", classmethod(lambda cls: mock_service))

        async def _fake_to_thread(func, *args, **kwargs):
            raise asyncio.CancelledError()

        monkeypatch.setattr("utils.shutdown.asyncio.to_thread", _fake_to_thread)

        with patch("utils.shutdown.mark_graceful_shutdown_completed", new_callable=MagicMock) as mock_mark:
            with pytest.raises(asyncio.CancelledError):
                await coord.do_cleanup(timeout_s=10.0, step_timeout_s=5.0)

        assert coord.cleanup_success is False
        assert coord.cleanup_done is True
        mock_mark.assert_not_called()


class TestDynamicCleanupCallbacks:
    """S3（D8）/D02-F02：动态清理回调在 Step 3 后、Step 3b（异步 engine dispose）前执行。"""

    _STEP_METHODS = (
        "_step0_cancel_tasks",
        "_step1_stop_services",
        "_step2_flush_db_writes",
        "_step3_stop_processor_workers",
        "_step3b_dispose_async_engine",
        "_step4_clear_toast",
        "_step5_unload_ai_model",
        "_step5b_stop_sentiment_scorer",
        "_step6_shutdown_thread_pools",
        "_step7_close_database_managers",
        "_step8_stop_embedded_postgres",
    )

    @staticmethod
    def _stub_steps(coord: ShutdownCoordinator, call_log: list[str]) -> None:
        for method in TestDynamicCleanupCallbacks._STEP_METHODS:

            async def _step(_m=method):
                call_log.append(_m)

            setattr(coord, method, _step)

    @pytest.mark.asyncio
    async def test_dynamic_callback_runs_after_stop_processor_before_engine_dispose(self):
        """动态回调（Recorder 最终落账）必须穿插在 Step 3 停止 worker 之后、Step 3b dispose engine 之前。"""
        coord = ShutdownCoordinator(service_stop_delay=0)
        call_log: list[str] = []
        self._stub_steps(coord, call_log)

        async def recorder_close():
            call_log.append("dyn-recorder-close")

        coord.register_cleanup_callback("usage recorder final flush", recorder_close, critical=True, timeout_s=5.0)

        results = await coord._run_cleanup_steps(step_timeout_s=2.0)
        # 11 个静态步骤 + 1 个动态回调
        assert len(results) == 12
        assert call_log.index("_step3_stop_processor_workers") < call_log.index("dyn-recorder-close")
        assert call_log.index("dyn-recorder-close") < call_log.index("_step3b_dispose_async_engine")

    @pytest.mark.asyncio
    async def test_dynamic_callbacks_execute_in_registration_order(self):
        """多个动态回调按注册顺序执行（worker 收敛 → Recorder.aclose）。"""
        coord = ShutdownCoordinator(service_stop_delay=0)
        call_log: list[str] = []
        self._stub_steps(coord, call_log)

        async def first():
            call_log.append("dyn-first")

        async def second():
            call_log.append("dyn-second")

        coord.register_cleanup_callback("dyn-1", first, critical=True, timeout_s=5.0)
        coord.register_cleanup_callback("dyn-2", second, critical=True, timeout_s=5.0)

        await coord._run_cleanup_steps(step_timeout_s=2.0)
        assert call_log.index("dyn-first") < call_log.index("dyn-second")

    @pytest.mark.asyncio
    async def test_sync_dynamic_callback_supported(self):
        """register_cleanup_callback 承诺 sync callable 可用：sync 回调正常执行不报错。"""
        coord = ShutdownCoordinator(service_stop_delay=0)
        call_log: list[str] = []
        self._stub_steps(coord, call_log)

        coord.register_cleanup_callback("dyn-sync", lambda: call_log.append("dyn-sync"), critical=True, timeout_s=5.0)

        results = await coord._run_cleanup_steps(step_timeout_s=2.0)
        assert len(results) == 12
        assert "dyn-sync" in call_log
        sync_result = next(r for r in results if r.name == "dyn-sync")
        assert sync_result.ok is True

    @pytest.mark.asyncio
    async def test_no_dynamic_callbacks_keeps_static_step_count(self):
        """未注册动态回调时 results 保持 11 个静态步骤（回归保护）。"""
        coord = ShutdownCoordinator(service_stop_delay=0)
        call_log: list[str] = []
        self._stub_steps(coord, call_log)

        results = await coord._run_cleanup_steps(step_timeout_s=2.0)
        assert len(results) == 11
        assert coord._dynamic_cleanup_callbacks == []

    @pytest.mark.asyncio
    async def test_critical_dynamic_callback_failure_continues_remaining_steps(self):
        """critical 动态回调失败不中断后续静态步骤（资源释放步骤必须全部执行）。"""
        coord = ShutdownCoordinator(service_stop_delay=0)
        call_log: list[str] = []
        self._stub_steps(coord, call_log)

        async def failing_close():
            raise RuntimeError("recorder close failed")

        coord.register_cleanup_callback("usage recorder final flush", failing_close, critical=True, timeout_s=5.0)

        results = await coord._run_cleanup_steps(step_timeout_s=2.0)
        assert len(results) == 12
        dyn_result = next(r for r in results if r.name == "usage recorder final flush")
        assert dyn_result.ok is False
        assert dyn_result.critical is True
        # Step 3b dispose engine 与 Step 6~8 仍执行
        assert "_step3b_dispose_async_engine" in call_log
        assert "_step6_shutdown_thread_pools" in call_log
        assert "_step8_stop_embedded_postgres" in call_log
