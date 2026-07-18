import threading

import pytest
from unittest.mock import MagicMock, patch

from data.cache.cache_manager import CacheManager
from data.data_processor import DataProcessor
from data.domain_services.market_data_service import MarketDataService  # noqa: F401
from data.external.akshare_concept_client import AkshareConceptClient  # noqa: F401
from data.external.tushare_client import TushareClient  # noqa: F401
from services.ai_service import AIService
from services.local_model_manager import LocalModelManager
from services.news_subscription_service import NewsSubscriptionService
from services.task_manager import TaskManager, AppTask, TaskStatus
from strategies.all_strategies import StrategyManager  # noqa: F401
from utils.scheduler_service import SchedulerService
from utils.singleton_registry import _registry
from utils.thread_pool import ThreadPoolManager  # noqa: F401

pytestmark = pytest.mark.unit

# Phase 4 Task 4.1: 模块收集时显式导入全部 12 个 @register_singleton 模块，
# 触发装饰器注册到 _registry。随后动态枚举 _registry 构造参数化测试用例，
# 自动覆盖后续新增的注册单例，无需手工维护清单。上述 noqa: F401 标注的
# 导入仅用于 side-effect 注册，不在测试中直接引用类名。
_REGISTERED_SINGLETON_CLASSES: list[type[object]] = list(_registry)
_SINGLETON_IDS: list[str] = [c.__name__ for c in _REGISTERED_SINGLETON_CLASSES]


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


@pytest.mark.parametrize(
    "cls",
    _REGISTERED_SINGLETON_CLASSES,
    ids=_SINGLETON_IDS,
)
class TestAllRegisteredSingletonsReset:
    """Phase 4 Task 4.1: 动态枚举 _registry 中所有 @register_singleton 注册项，验证隔离契约。

    通过 _registry 动态枚举所有注册单例，自动覆盖后续新增项，无需手工维护清单。
    - R15: 每个 @register_singleton 类必须有可调用的 _reset_singleton classmethod
    - R7:  _reset_singleton() 后 _instance 必须为 None
    覆盖此前缺失的 AIService / TushareClient / AkshareConceptClient /
    LocalModelManager / StrategyManager / MarketDataService /
    NewsSubscriptionService 7 项。
    """

    def test_has_reset_singleton_classmethod(self, cls: type[object]) -> None:
        """R15: 每个注册单例必须有可调用的 _reset_singleton classmethod。"""
        assert hasattr(cls, "_reset_singleton"), f"{cls.__name__} 缺少 _reset_singleton classmethod (R15)"
        assert callable(cls._reset_singleton)  # type: ignore[attr-defined]

    def test_reset_singleton_clears_instance(self, cls: type[object]) -> None:
        """R7: _reset_singleton() 后 _instance 必须为 None。

        用 MagicMock 占位 _instance，避免触发真实资源初始化
        （如 ThreadPoolExecutor / 后台任务 / 子进程），专注验证 reset 行为。
        """
        cls._instance = MagicMock()  # type: ignore[attr-defined]
        cls._reset_singleton()  # type: ignore[attr-defined]
        assert cls._instance is None, (  # type: ignore[attr-defined]
            f"{cls.__name__}._reset_singleton 未清空 _instance (R7)"
        )
