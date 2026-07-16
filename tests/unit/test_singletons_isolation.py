import threading

import pytest
from unittest.mock import patch

from data.cache.cache_manager import CacheManager
from data.data_processor import DataProcessor
from services.ai_service import AIService
from services.local_model_manager import LocalModelManager
from services.news_subscription_service import NewsSubscriptionService
from services.task_manager import TaskManager, AppTask, TaskStatus
from utils.scheduler_service import SchedulerService

pytestmark = pytest.mark.unit


@pytest.mark.unit
class TestSingletonIsolation:
    def test_data_processor_reset_clears_instance(self):
        DataProcessor()
        assert DataProcessor._instance is not None
        DataProcessor._reset_singleton()
        assert DataProcessor._instance is None

    def test_data_processor_returns_same_instance(self):
        dp1 = DataProcessor()
        dp2 = DataProcessor()
        assert dp1 is dp2
        DataProcessor._reset_singleton()

    def test_scheduler_service_reset_clears_instance(self):
        SchedulerService()
        assert SchedulerService._instance is not None
        SchedulerService._reset_singleton()
        assert SchedulerService._instance is None

    def test_scheduler_service_returns_same_instance(self):
        ss1 = SchedulerService()
        ss2 = SchedulerService()
        assert ss1 is ss2
        SchedulerService._reset_singleton()

    def test_local_model_manager_reset_clears_instance(self):
        lmm = LocalModelManager()
        LocalModelManager._instance = lmm
        assert LocalModelManager._instance is not None
        LocalModelManager._reset_singleton()
        assert LocalModelManager._instance is None

    def test_data_processor_thread_safety(self):
        instances = []

        def create_instance():
            dp = DataProcessor()
            instances.append(id(dp))

        threads = [threading.Thread(target=create_instance) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(set(instances)) == 1, "Multiple instances created across threads"
        DataProcessor._reset_singleton()

    def test_scheduler_service_thread_safety(self):
        instances = []

        def create_instance():
            ss = SchedulerService()
            instances.append(id(ss))

        threads = [threading.Thread(target=create_instance) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(set(instances)) == 1, "Multiple instances created across threads"
        SchedulerService._reset_singleton()

    def test_reset_between_tests_prevents_leakage(self):
        dp1 = DataProcessor()
        first_id = id(dp1)
        DataProcessor._reset_singleton()
        assert DataProcessor._instance is None
        dp2 = DataProcessor()
        assert id(dp2) != first_id or DataProcessor._instance is not None
        DataProcessor._reset_singleton()


class TestCacheManagerSingletonIsolation:
    def test_cache_manager_reset_clears_instance(self):
        CacheManager._instance = None
        CacheManager._initialized = False
        with patch("data.cache.cache_manager.ConfigHandler.get_db_url", return_value=None):
            CacheManager()
            assert CacheManager._instance is not None
            CacheManager._reset_singleton()
            assert CacheManager._instance is None

    def test_cache_manager_reset_clears_initialized_flag(self):
        CacheManager._instance = None
        CacheManager._initialized = False
        with patch("data.cache.cache_manager.ConfigHandler.get_db_url", return_value=None):
            CacheManager()
            assert CacheManager._initialized is True
            CacheManager._reset_singleton()
            assert CacheManager._initialized is False

    def test_cache_manager_returns_same_instance(self):
        CacheManager._instance = None
        CacheManager._initialized = False
        with patch("data.cache.cache_manager.ConfigHandler.get_db_url", return_value=None):
            mgr1 = CacheManager()
            mgr2 = CacheManager()
            assert mgr1 is mgr2
            CacheManager._reset_singleton()

    def test_cache_manager_thread_safety(self):
        CacheManager._instance = None
        CacheManager._initialized = False
        instances = []

        with patch("data.cache.cache_manager.ConfigHandler.get_db_url", return_value=None):

            def create_instance():
                CacheManager()
                instances.append(id(CacheManager._instance))

            threads = [threading.Thread(target=create_instance) for _ in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        assert len(set(instances)) == 1, "Multiple instances created across threads"
        CacheManager._reset_singleton()


class TestTaskManagerSingletonIsolation:
    def test_task_manager_reset_clears_instance(self):
        TaskManager._instance = None
        TaskManager._initialized = False
        with (
            patch("services.task_manager.ThreadPoolManager"),
        ):
            TaskManager()
            assert TaskManager._instance is not None
            TaskManager._reset_singleton()
            assert TaskManager._instance is None

    def test_task_manager_reset_clears_initialized_flag(self):
        TaskManager._instance = None
        TaskManager._initialized = False
        with (
            patch("services.task_manager.ThreadPoolManager"),
        ):
            TaskManager()
            assert TaskManager._initialized is True
            TaskManager._reset_singleton()
            assert TaskManager._initialized is False

    def test_task_manager_returns_same_instance(self):
        TaskManager._instance = None
        TaskManager._initialized = False
        with (
            patch("services.task_manager.ThreadPoolManager"),
        ):
            mgr1 = TaskManager()
            mgr2 = TaskManager()
            assert mgr1 is mgr2
            TaskManager._reset_singleton()

    def test_task_manager_new_instance_has_empty_queue(self):
        TaskManager._instance = None
        TaskManager._initialized = False
        with (
            patch("services.task_manager.ThreadPoolManager"),
        ):
            mgr = TaskManager()
            t = AppTask(name="test", status=TaskStatus.RUNNING)
            mgr._tasks[t.id] = t
            assert len(mgr._tasks) == 1

            TaskManager._reset_singleton()

            TaskManager._instance = None
            TaskManager._initialized = False
            with (
                patch("services.task_manager.ThreadPoolManager"),
            ):
                mgr2 = TaskManager()
                assert len(mgr2._tasks) == 0, "New instance should have empty _tasks queue"
            TaskManager._reset_singleton()

    def test_task_manager_thread_safety(self):
        TaskManager._instance = None
        TaskManager._initialized = False
        instances = []

        with (
            patch("services.task_manager.ThreadPoolManager"),
        ):

            def create_instance():
                TaskManager()
                instances.append(id(TaskManager._instance))

            threads = [threading.Thread(target=create_instance) for _ in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        assert len(set(instances)) == 1, "Multiple instances created across threads"
        TaskManager._reset_singleton()


class TestSingletonResetClearsLoopLocal:
    """Verify _reset_singleton invokes del_loop_local with correct keys (Stage 2 fix)."""

    def test_cache_manager_reset_clears_loop_local_keys(self):
        with patch("data.cache.cache_manager.del_loop_local") as mock_del:
            CacheManager._reset_singleton()
            keys = {call.args[0] for call in mock_del.call_args_list}
            assert keys == {"cache_maint_event", "cache_init_lock"}

    def test_ai_service_reset_clears_loop_local_keys(self):
        with patch("services.ai_service.del_loop_local") as mock_del:
            AIService._reset_singleton()
            keys = {call.args[0] for call in mock_del.call_args_list}
            assert keys == {"ai_setup_lock", "ai_analysis_semaphore", "ai_news_semaphore"}

    def test_data_processor_reset_clears_loop_local_keys(self):
        with patch("data.data_processor.del_loop_local") as mock_del:
            DataProcessor._reset_singleton()
            mock_del.assert_called_once_with("processor_cancel_evt")

    def test_news_subscription_reset_clears_loop_local_keys(self):
        with patch("services.news_subscription_service.del_loop_local") as mock_del:
            NewsSubscriptionService._reset_singleton()
            keys = {call.args[0] for call in mock_del.call_args_list}
            assert keys == {"news_processing_queue", "news_queue_put_lock"}

    def test_local_model_manager_reset_clears_loop_local_keys(self):
        with patch("services.local_model_manager.del_loop_local") as mock_del:
            LocalModelManager._reset_singleton()
            mock_del.assert_called_once_with("local_load_lock")

    def test_news_subscription_reset_clears_listeners(self):
        """Verify _reset_singleton clears _listeners and _alert_listeners on existing instance."""
        from services.news_subscription_service import NewsSubscriptionService

        # Simulate an existing instance with listeners
        inst = NewsSubscriptionService.__new__(NewsSubscriptionService)
        inst._listeners = {"dummy_listener"}
        inst._alert_listeners = {"dummy_alert"}
        NewsSubscriptionService._instance = inst
        NewsSubscriptionService._initialized = True

        with patch("services.news_subscription_service.del_loop_local"):
            NewsSubscriptionService._reset_singleton()

        assert inst._listeners == set()
        assert inst._alert_listeners == set()
