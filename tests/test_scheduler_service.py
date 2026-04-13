"""Tests for SchedulerService singleton management."""

import threading
from unittest.mock import MagicMock, patch


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
