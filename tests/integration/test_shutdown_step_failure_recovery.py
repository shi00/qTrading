import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils.shutdown import ShutdownCoordinator


@pytest.mark.unit
class TestShutdownStepFailureRecovery:
    @pytest.mark.asyncio
    async def test_non_critical_step_failure_does_not_fail_cleanup(self, mock_singletons):
        mock_singletons["TaskManager"]._instance.cancel_all_running_async = AsyncMock()
        coordinator = ShutdownCoordinator(page=None, service_stop_delay=0)

        async def _failing_step4():
            raise RuntimeError("toast cleanup failed")

        with (
            patch("asyncio.sleep", new_callable=AsyncMock),
            patch.object(coordinator, "_step4_clear_toast", side_effect=_failing_step4),
        ):
            ok = await coordinator.do_cleanup()

        step4 = next(r for r in coordinator.step_results if r.name == "Step 4")
        assert step4.ok is False
        assert step4.critical is False
        assert ok is True

    @pytest.mark.asyncio
    async def test_multiple_critical_failures_all_recorded(self, mock_singletons):
        mock_singletons["TaskManager"]._instance.cancel_all_running_async = AsyncMock(
            side_effect=RuntimeError("cancel failed")
        )
        coordinator = ShutdownCoordinator(page=None, service_stop_delay=0)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            ok = await coordinator.do_cleanup()

        assert ok is False
        failed = [r for r in coordinator.step_results if r.critical and not r.ok]
        assert len(failed) >= 1
        failed_names = {r.name for r in failed}
        assert "Step 0" in failed_names

    @pytest.mark.asyncio
    async def test_critical_failure_continues_remaining_steps(self, mock_singletons):
        mock_singletons["TaskManager"]._instance.cancel_all_running_async = AsyncMock(
            side_effect=RuntimeError("step0 failed")
        )
        coordinator = ShutdownCoordinator(page=None, service_stop_delay=0)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            ok = await coordinator.do_cleanup()

        assert ok is False
        step0 = next(r for r in coordinator.step_results if r.name == "Step 0")
        assert step0.ok is False
        assert len(coordinator.step_results) == 7

    @pytest.mark.asyncio
    async def test_step_timeout_continues_remaining_steps(self, mock_singletons):
        coordinator = ShutdownCoordinator(page=None, service_stop_delay=0)

        async def _blocking_step0():
            await asyncio.sleep(1)

        with patch.object(coordinator, "_step0_cancel_tasks", side_effect=_blocking_step0):
            await coordinator.do_cleanup(timeout_s=3.0, step_timeout_s=0.3)

        step0 = next(r for r in coordinator.step_results if r.name == "Step 0")
        assert step0.ok is False
        assert step0.timed_out is True
        assert len(coordinator.step_results) == 7

    @pytest.mark.asyncio
    async def test_step_result_error_message_preserved(self, mock_singletons):
        mock_singletons["TaskManager"]._instance.cancel_all_running_async = AsyncMock(
            side_effect=ValueError("specific error message")
        )
        coordinator = ShutdownCoordinator(page=None, service_stop_delay=0)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await coordinator.do_cleanup()

        step0 = next(r for r in coordinator.step_results if r.name == "Step 0")
        assert "specific error message" in step0.error

    @pytest.mark.asyncio
    async def test_all_steps_have_results(self, mock_singletons):
        coordinator = ShutdownCoordinator(page=None, service_stop_delay=0)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await coordinator.do_cleanup()

        assert len(coordinator.step_results) == 7
        names = [r.name for r in coordinator.step_results]
        assert names == ["Step 0", "Step 1", "Step 2", "Step 3", "Step 4", "Step 5", "Step 6"]

    @pytest.mark.asyncio
    async def test_step_result_elapsed_ms_positive(self, mock_singletons):
        coordinator = ShutdownCoordinator(page=None, service_stop_delay=0)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await coordinator.do_cleanup()

        for r in coordinator.step_results:
            assert r.elapsed_ms >= 0


@pytest.fixture
def mock_singletons():
    from services.task_manager import TaskManager
    from data.external.news_subscription import NewsSubscriptionService
    from data.data_processor import DataProcessor
    from data.domain_services.market_data_service import MarketDataService
    from services.local_model_manager import LocalModelManager
    from utils.thread_pool import ThreadPoolManager
    from utils.scheduler_service import SchedulerService

    orig_tm = TaskManager._instance
    orig_news = NewsSubscriptionService._instance
    orig_dp = DataProcessor._instance
    orig_mds = MarketDataService._instance
    orig_llm = LocalModelManager._instance
    orig_tp = ThreadPoolManager._instance
    svc = SchedulerService()
    orig_scheduler_running = getattr(svc.scheduler, "running", None) if hasattr(svc, "scheduler") else None
    orig_scheduler_stop = getattr(svc, "stop", None)

    TaskManager._instance = AsyncMock()
    NewsSubscriptionService._instance = AsyncMock()
    DataProcessor._instance = AsyncMock()
    MarketDataService._instance = AsyncMock()
    LocalModelManager._instance = MagicMock()
    LocalModelManager._instance._llm = MagicMock()
    ThreadPoolManager._instance = MagicMock()
    svc.scheduler = MagicMock()
    svc.scheduler.running = True
    svc.stop = MagicMock()

    yield {
        "TaskManager": TaskManager,
        "scheduler": svc,
        "NewsSubscriptionService": NewsSubscriptionService,
        "DataProcessor": DataProcessor,
        "MarketDataService": MarketDataService,
        "LocalModelManager": LocalModelManager,
        "ThreadPoolManager": ThreadPoolManager,
    }

    TaskManager._instance = orig_tm
    NewsSubscriptionService._instance = orig_news
    DataProcessor._instance = orig_dp
    MarketDataService._instance = orig_mds
    LocalModelManager._instance = orig_llm
    ThreadPoolManager._instance = orig_tp
    if orig_scheduler_running is not None:
        svc.scheduler.running = orig_scheduler_running
    if orig_scheduler_stop is not None:
        svc.stop = orig_scheduler_stop
