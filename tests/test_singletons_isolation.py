import threading

import pytest

from data.data_processor import DataProcessor
from services.local_model_manager import LocalModelManager
from utils.scheduler_service import SchedulerService


@pytest.mark.unit
class TestSingletonIsolation:
    def test_data_processor_reset_clears_instance(self):
        DataProcessor._instance = None
        assert DataProcessor._instance is None
        DataProcessor()
        assert DataProcessor._instance is not None
        DataProcessor._reset_singleton()
        assert DataProcessor._instance is None

    def test_data_processor_returns_same_instance(self):
        DataProcessor._instance = None
        dp1 = DataProcessor()
        dp2 = DataProcessor()
        assert dp1 is dp2
        DataProcessor._reset_singleton()

    def test_scheduler_service_reset_clears_instance(self):
        original = SchedulerService._instance
        SchedulerService._instance = None
        assert SchedulerService._instance is None
        SchedulerService()
        assert SchedulerService._instance is not None
        SchedulerService._reset_singleton()
        assert SchedulerService._instance is None
        SchedulerService._instance = original

    def test_scheduler_service_returns_same_instance(self):
        original = SchedulerService._instance
        SchedulerService._instance = None
        ss1 = SchedulerService()
        ss2 = SchedulerService()
        assert ss1 is ss2
        SchedulerService._reset_singleton()
        SchedulerService._instance = original

    def test_local_model_manager_reset_clears_instance(self):
        original = LocalModelManager._instance
        LocalModelManager._instance = None
        assert LocalModelManager._instance is None
        lmm = LocalModelManager()
        LocalModelManager._instance = lmm
        assert LocalModelManager._instance is not None
        LocalModelManager._reset_singleton()
        assert LocalModelManager._instance is None
        LocalModelManager._instance = original

    def test_data_processor_thread_safety(self):
        DataProcessor._instance = None
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
        original = SchedulerService._instance
        SchedulerService._instance = None
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
        SchedulerService._instance = original

    def test_reset_between_tests_prevents_leakage(self):
        DataProcessor._instance = None
        dp1 = DataProcessor()
        first_id = id(dp1)
        DataProcessor._reset_singleton()
        assert DataProcessor._instance is None
        dp2 = DataProcessor()
        assert id(dp2) != first_id or DataProcessor._instance is not None
        DataProcessor._reset_singleton()
