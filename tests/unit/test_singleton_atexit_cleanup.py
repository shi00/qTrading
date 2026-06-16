"""SINGLETON-001: Unit tests for _atexit_cleanup class methods on registered singletons.

Covers:
- CacheManager._atexit_cleanup() — disposes SQLAlchemy sync engine, sets _disposed=True
- DataProcessor._atexit_cleanup() — sets cancel event
- SchedulerService._atexit_cleanup() — calls scheduler.shutdown(wait=False)
"""

from unittest.mock import MagicMock, patch

from data.cache.cache_manager import CacheManager
from data.data_processor import DataProcessor
from utils.scheduler_service import SchedulerService


# ---------------------------------------------------------------------------
# CacheManager._atexit_cleanup
# ---------------------------------------------------------------------------


class TestCacheManagerAtexitCleanup:
    def test_disposes_sync_engine_and_sets_disposed(self):
        """_atexit_cleanup calls engine.sync_engine.dispose() and sets _disposed=True."""
        mgr = CacheManager.__new__(CacheManager)
        mgr._disposed = False
        mock_sync_engine = MagicMock()
        mock_engine = MagicMock()
        mock_engine.sync_engine = mock_sync_engine
        mgr.engine = mock_engine

        CacheManager._instance = mgr
        CacheManager._atexit_cleanup()

        mock_sync_engine.dispose.assert_called_once()
        assert mgr._disposed is True

    def test_skips_dispose_when_already_disposed(self):
        """_atexit_cleanup does NOT call dispose when _disposed is already True."""
        mgr = CacheManager.__new__(CacheManager)
        mgr._disposed = True
        mock_sync_engine = MagicMock()
        mock_engine = MagicMock()
        mock_engine.sync_engine = mock_sync_engine
        mgr.engine = mock_engine

        CacheManager._instance = mgr
        CacheManager._atexit_cleanup()

        mock_sync_engine.dispose.assert_not_called()

    def test_no_error_when_instance_is_none(self):
        """_atexit_cleanup does not raise when _instance is None."""
        CacheManager._instance = None
        CacheManager._atexit_cleanup()  # should not raise

    def test_no_error_when_engine_is_none(self):
        """_atexit_cleanup does not raise when engine is None."""
        mgr = CacheManager.__new__(CacheManager)
        mgr._disposed = False
        mgr.engine = None

        CacheManager._instance = mgr
        CacheManager._atexit_cleanup()  # should not raise

    def test_no_error_when_dispose_raises(self):
        """_atexit_cleanup swallows exceptions from sync_engine.dispose()."""
        mgr = CacheManager.__new__(CacheManager)
        mgr._disposed = False
        mock_sync_engine = MagicMock()
        mock_sync_engine.dispose.side_effect = RuntimeError("dispose failed")
        mock_engine = MagicMock()
        mock_engine.sync_engine = mock_sync_engine
        mgr.engine = mock_engine

        CacheManager._instance = mgr
        CacheManager._atexit_cleanup()  # should not raise


# ---------------------------------------------------------------------------
# DataProcessor._atexit_cleanup
# ---------------------------------------------------------------------------


class TestDataProcessorAtexitCleanup:
    def test_sets_cancel_event(self):
        """_atexit_cleanup sets the cancel event via get_loop_local(strict=False)."""
        dp = DataProcessor.__new__(DataProcessor)
        mock_event = MagicMock()

        DataProcessor._instance = dp
        with patch("utils.loop_local.get_loop_local", return_value=mock_event):
            DataProcessor._atexit_cleanup()

        mock_event.set.assert_called_once()

    def test_no_error_when_instance_is_none(self):
        """_atexit_cleanup does not raise when _instance is None."""
        DataProcessor._instance = None
        DataProcessor._atexit_cleanup()  # should not raise

    def test_no_error_when_get_loop_local_raises(self):
        """_atexit_cleanup swallows exceptions from get_loop_local()."""
        dp = DataProcessor.__new__(DataProcessor)

        DataProcessor._instance = dp
        with patch("utils.loop_local.get_loop_local", side_effect=RuntimeError("no event loop")):
            DataProcessor._atexit_cleanup()  # should not raise

    def test_no_error_when_event_is_none(self):
        """_atexit_cleanup does nothing when get_loop_local returns None (no event created yet)."""
        dp = DataProcessor.__new__(DataProcessor)

        DataProcessor._instance = dp
        with patch("utils.loop_local.get_loop_local", return_value=None):
            DataProcessor._atexit_cleanup()  # should not raise


# ---------------------------------------------------------------------------
# SchedulerService._atexit_cleanup
# ---------------------------------------------------------------------------


class TestSchedulerServiceAtexitCleanup:
    def test_shutdown_running_scheduler(self):
        """_atexit_cleanup calls scheduler.shutdown(wait=False) when running."""
        svc = SchedulerService.__new__(SchedulerService)
        mock_scheduler = MagicMock()
        mock_scheduler.running = True
        svc.scheduler = mock_scheduler

        SchedulerService._instance = svc
        SchedulerService._atexit_cleanup()

        mock_scheduler.shutdown.assert_called_once_with(wait=False)

    def test_skips_shutdown_when_not_running(self):
        """_atexit_cleanup does NOT call shutdown when scheduler is not running."""
        svc = SchedulerService.__new__(SchedulerService)
        mock_scheduler = MagicMock()
        mock_scheduler.running = False
        svc.scheduler = mock_scheduler

        SchedulerService._instance = svc
        SchedulerService._atexit_cleanup()

        mock_scheduler.shutdown.assert_not_called()

    def test_no_error_when_instance_is_none(self):
        """_atexit_cleanup does not raise when _instance is None."""
        SchedulerService._instance = None
        SchedulerService._atexit_cleanup()  # should not raise

    def test_no_error_when_no_scheduler_attr(self):
        """_atexit_cleanup does not raise when instance has no scheduler attribute."""
        svc = SchedulerService.__new__(SchedulerService)
        # Deliberately do NOT set svc.scheduler

        SchedulerService._instance = svc
        SchedulerService._atexit_cleanup()  # should not raise

    def test_no_error_when_shutdown_raises(self):
        """_atexit_cleanup swallows exceptions from scheduler.shutdown()."""
        svc = SchedulerService.__new__(SchedulerService)
        mock_scheduler = MagicMock()
        mock_scheduler.running = True
        mock_scheduler.shutdown.side_effect = RuntimeError("shutdown failed")
        svc.scheduler = mock_scheduler

        SchedulerService._instance = svc
        SchedulerService._atexit_cleanup()  # should not raise
