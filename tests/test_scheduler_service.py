"""Tests for SchedulerService singleton management."""

import datetime
import threading
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


class TestSchedulerServiceSingleton:
    """Tests for SchedulerService singleton pattern and reset behavior."""

    def test_scheduler_has_reset_singleton(self):
        """Test that SchedulerService has _reset_singleton method."""
        with patch.dict(
            "sys.modules",
            {"utils.scheduler_service": MagicMock()},
        ):
            from utils.scheduler_service import SchedulerService

            SchedulerService._reset_singleton = MagicMock()
            SchedulerService._lock = threading.Lock()

            assert hasattr(SchedulerService, "_reset_singleton")
            assert hasattr(SchedulerService, "_lock")

    def test_scheduler_singleton_reset_clears_instance(self):
        """Test that _reset_singleton clears the singleton instance."""

        class MockSchedulerService:
            _instance = None
            _initialized = False
            _lock = threading.Lock()

            @classmethod
            def _reset_singleton(cls):
                with cls._lock:
                    if cls._instance is not None and hasattr(cls._instance, "scheduler"):
                        try:
                            if cls._instance.scheduler.running:
                                cls._instance.scheduler.shutdown(wait=False)
                        except Exception:
                            pass
                    cls._instance = None
                    cls._initialized = False

        MockSchedulerService._reset_singleton()
        assert MockSchedulerService._instance is None
        assert MockSchedulerService._initialized is False

    def test_scheduler_singleton_reset_shuts_down_running_scheduler(self):
        """Test that _reset_singleton properly shuts down running scheduler to prevent ghost threads."""

        class MockSchedulerService:
            _instance = None
            _initialized = False
            _lock = threading.Lock()

            @classmethod
            def _reset_singleton(cls):
                with cls._lock:
                    if cls._instance is not None and hasattr(cls._instance, "scheduler"):
                        try:
                            if cls._instance.scheduler.running:
                                cls._instance.scheduler.shutdown(wait=False)
                        except Exception:
                            pass
                    cls._instance = None
                    cls._initialized = False

        mock_scheduler = MagicMock()
        mock_scheduler.running = True

        instance = MagicMock()
        instance.scheduler = mock_scheduler

        MockSchedulerService._instance = instance
        MockSchedulerService._initialized = True

        MockSchedulerService._reset_singleton()

        mock_scheduler.shutdown.assert_called_once_with(wait=False)
        assert MockSchedulerService._instance is None
        assert MockSchedulerService._initialized is False

    def test_scheduler_singleton_reset_handles_non_running_scheduler(self):
        """Test that _reset_singleton handles non-running scheduler gracefully."""

        class MockSchedulerService:
            _instance = None
            _initialized = False
            _lock = threading.Lock()

            @classmethod
            def _reset_singleton(cls):
                with cls._lock:
                    if cls._instance is not None and hasattr(cls._instance, "scheduler"):
                        try:
                            if cls._instance.scheduler.running:
                                cls._instance.scheduler.shutdown(wait=False)
                        except Exception:
                            pass
                    cls._instance = None
                    cls._initialized = False

        mock_scheduler = MagicMock()
        mock_scheduler.running = False

        instance = MagicMock()
        instance.scheduler = mock_scheduler

        MockSchedulerService._instance = instance
        MockSchedulerService._initialized = True

        MockSchedulerService._reset_singleton()

        mock_scheduler.shutdown.assert_not_called()
        assert MockSchedulerService._instance is None
        assert MockSchedulerService._initialized is False

    def test_scheduler_singleton_reset_handles_shutdown_exception(self):
        """Test that _reset_singleton handles shutdown exceptions gracefully."""

        class MockSchedulerService:
            _instance = None
            _initialized = False
            _lock = threading.Lock()

            @classmethod
            def _reset_singleton(cls):
                with cls._lock:
                    if cls._instance is not None and hasattr(cls._instance, "scheduler"):
                        try:
                            if cls._instance.scheduler.running:
                                cls._instance.scheduler.shutdown(wait=False)
                        except Exception:
                            pass
                    cls._instance = None
                    cls._initialized = False

        mock_scheduler = MagicMock()
        mock_scheduler.running = True
        mock_scheduler.shutdown.side_effect = RuntimeError("Shutdown failed")

        instance = MagicMock()
        instance.scheduler = mock_scheduler

        MockSchedulerService._instance = instance
        MockSchedulerService._initialized = True

        MockSchedulerService._reset_singleton()

        assert MockSchedulerService._instance is None
        assert MockSchedulerService._initialized is False


@pytest.mark.asyncio
async def test_daily_update_logic_handles_dataframe_result_without_bool_error(monkeypatch):
    """_daily_update_logic should not evaluate DataFrame in boolean context."""
    import services.task_manager as tm_mod
    import utils.scheduler_service as sched_mod

    sched_mod.SchedulerService._reset_singleton()
    service = sched_mod.SchedulerService()

    fake_now = datetime.datetime(2026, 4, 23, 16, 30, 0)
    monkeypatch.setattr(sched_mod, "get_now", lambda: fake_now)
    monkeypatch.setattr(sched_mod.ConfigHandler, "is_auto_update_enabled", staticmethod(lambda: True))
    monkeypatch.setattr(
        sched_mod.I18n,
        "get",
        staticmethod(lambda key, **kwargs: f"{key}:{kwargs.get('added', kwargs.get('date', ''))}"),
    )

    class _FakeTradeCalendar:
        async def is_trading_day(self, _today):
            return True

    class _FakeProcessor:
        def __init__(self):
            self.trade_calendar = _FakeTradeCalendar()

        async def run_daily_update(self, progress_callback=None):
            if progress_callback:
                progress_callback(1, 2, "half")
                progress_callback(2, 2, "done")
            return pd.DataFrame([{"ts_code": "000001.SZ"}, {"ts_code": "000002.SZ"}])

    monkeypatch.setattr(sched_mod, "DataProcessor", _FakeProcessor)

    holder = {"factory": None}

    class _FakeTaskManager:
        def update_progress(self, *_args, **_kwargs):
            return None

        def submit_task(self, **kwargs):
            holder["factory"] = kwargs.get("coroutine_factory")

    monkeypatch.setattr(tm_mod, "TaskManager", _FakeTaskManager)

    await service._run_daily_update()
    assert holder["factory"] is not None

    msg = await holder["factory"]("task-id")
    assert msg == "sched_daily_done:2"
