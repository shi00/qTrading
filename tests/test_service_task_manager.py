"""
Tests for TaskManager service.

验证任务管理器的生命周期管理、并发控制和持久化功能。
"""

import asyncio
import datetime
import unittest
from unittest.mock import MagicMock

from services.task_manager import (
    TERMINAL_STATUSES,
    AppTask,
    TaskManager,
    TaskStatus,
)


class TestTaskStatus(unittest.TestCase):
    """测试任务状态枚举"""

    def test_status_values(self):
        """状态值正确"""
        self.assertEqual(TaskStatus.QUEUED.value, "QUEUED")
        self.assertEqual(TaskStatus.RUNNING.value, "RUNNING")
        self.assertEqual(TaskStatus.COMPLETED.value, "COMPLETED")
        self.assertEqual(TaskStatus.FAILED.value, "FAILED")
        self.assertEqual(TaskStatus.CANCELLED.value, "CANCELLED")
        self.assertEqual(TaskStatus.INTERRUPTED.value, "INTERRUPTED")

    def test_terminal_statuses(self):
        """终态状态集合"""
        self.assertIn(TaskStatus.COMPLETED, TERMINAL_STATUSES)
        self.assertIn(TaskStatus.FAILED, TERMINAL_STATUSES)
        self.assertIn(TaskStatus.CANCELLED, TERMINAL_STATUSES)
        self.assertIn(TaskStatus.INTERRUPTED, TERMINAL_STATUSES)
        self.assertNotIn(TaskStatus.QUEUED, TERMINAL_STATUSES)
        self.assertNotIn(TaskStatus.RUNNING, TERMINAL_STATUSES)


class TestAppTask(unittest.TestCase):
    """测试任务数据类"""

    def test_task_creation(self):
        """任务创建"""
        task = AppTask(name="Test Task", task_type="Test")

        self.assertEqual(task.name, "Test Task")
        self.assertEqual(task.task_type, "Test")
        self.assertEqual(task.status, TaskStatus.QUEUED)
        self.assertEqual(task.progress, 0.0)
        self.assertIsNotNone(task.id)
        self.assertIsNotNone(task.created_at)

    def test_task_default_values(self):
        """默认值"""
        task = AppTask()

        self.assertEqual(task.name, "Unknown Task")
        self.assertEqual(task.task_type, "System")
        self.assertEqual(task.description, "Waiting...")
        self.assertFalse(task.cancellable)
        self.assertIsNone(task.started_at)
        self.assertIsNone(task.completed_at)
        self.assertIsNone(task.result)
        self.assertEqual(task.error, "")

    def test_task_id_uniqueness(self):
        """ID 唯一性"""
        task1 = AppTask()
        task2 = AppTask()

        self.assertNotEqual(task1.id, task2.id)

    def test_task_custom_values(self):
        """自定义值"""
        now = datetime.datetime.now()
        task = AppTask(
            id="custom_id",
            name="Custom",
            task_type="CustomType",
            status=TaskStatus.RUNNING,
            progress=0.5,
            description="In progress",
            cancellable=True,
            created_at=now,
            started_at=now,
        )

        self.assertEqual(task.id, "custom_id")
        self.assertEqual(task.status, TaskStatus.RUNNING)
        self.assertEqual(task.progress, 0.5)
        self.assertTrue(task.cancellable)


class TestTaskManagerSingleton(unittest.TestCase):
    """测试任务管理器单例模式"""

    def setUp(self):
        TaskManager._instance = None

    def test_singleton(self):
        """单例模式"""
        manager1 = TaskManager()
        manager2 = TaskManager()

        self.assertIs(manager1, manager2)

    def test_singleton_thread_safety(self):
        """单例线程安全"""
        import threading

        instances = []

        def create_instance():
            instances.append(TaskManager())

        threads = [threading.Thread(target=create_instance) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertTrue(all(inst is instances[0] for inst in instances))


class TestTaskManagerSubscribe(unittest.TestCase):
    """测试任务管理器订阅机制"""

    def setUp(self):
        TaskManager._instance = None
        self.manager = TaskManager()

    def test_subscribe(self):
        """订阅回调"""
        callback = MagicMock()
        self.manager.subscribe(callback)

        self.assertIn(callback, self.manager._subscribers)

    def test_unsubscribe(self):
        """取消订阅"""
        callback = MagicMock()
        self.manager.subscribe(callback)
        self.manager.unsubscribe(callback)

        self.assertNotIn(callback, self.manager._subscribers)

    def test_notify_subscribers(self):
        """通知订阅者"""
        callback = MagicMock()
        self.manager.subscribe(callback)
        callback.reset_mock()

        self.manager._notify_subscribers()

        callback.assert_called_once()

    def test_notify_multiple_subscribers(self):
        """通知多个订阅者"""
        callbacks = [MagicMock() for _ in range(3)]
        for cb in callbacks:
            self.manager.subscribe(cb)
            cb.reset_mock()

        self.manager._notify_subscribers()

        for cb in callbacks:
            self.assertEqual(cb.call_count, 1)


class TestTaskManagerGetTasks(unittest.TestCase):
    """测试任务管理器查询功能"""

    def setUp(self):
        TaskManager._instance = None
        self.manager = TaskManager()

    def test_get_all_tasks_empty(self):
        """空任务列表"""
        tasks = self.manager.get_all_tasks()

        self.assertEqual(len(tasks), 0)

    def test_get_task_not_found(self):
        """查询不存在的任务"""
        task = self.manager.get_task("nonexistent")

        self.assertIsNone(task)

    def test_get_all_tasks_with_tasks(self):
        """查询所有任务"""
        task1 = AppTask(id="task1", name="Task 1")
        task2 = AppTask(id="task2", name="Task 2")
        self.manager._tasks = {"task1": task1, "task2": task2}

        tasks = self.manager.get_all_tasks()

        self.assertEqual(len(tasks), 2)
        task_ids = [t.id for t in tasks]
        self.assertIn("task1", task_ids)
        self.assertIn("task2", task_ids)


class TestTaskManagerUpdateProgress(unittest.TestCase):
    """测试任务管理器进度更新"""

    def setUp(self):
        TaskManager._instance = None
        self.manager = TaskManager()

    def test_update_progress_running_task(self):
        """更新运行中任务进度"""
        task = AppTask(id="task1", name="Test", status=TaskStatus.RUNNING)
        self.manager._tasks = {"task1": task}

        self.manager.update_progress("task1", 0.5, "Half done")

        self.assertEqual(task.progress, 0.5)
        self.assertEqual(task.description, "Half done")

    def test_update_progress_clamp_values(self):
        """进度值钳制"""
        task = AppTask(id="task1", name="Test", status=TaskStatus.RUNNING)
        self.manager._tasks = {"task1": task}

        self.manager.update_progress("task1", 1.5)
        self.assertEqual(task.progress, 1.0)

        self.manager.update_progress("task1", -0.5)
        self.assertEqual(task.progress, 0.0)

    def test_update_progress_non_running_task(self):
        """非运行任务不更新"""
        task = AppTask(id="task1", name="Test", status=TaskStatus.QUEUED)
        self.manager._tasks = {"task1": task}

        self.manager.update_progress("task1", 0.5)

        self.assertEqual(task.progress, 0.0)


class TestTaskManagerCancel(unittest.TestCase):
    """测试任务管理器取消功能"""

    def setUp(self):
        TaskManager._instance = None
        self.manager = TaskManager()
        self.manager._loop = MagicMock()
        self.manager._loop.is_running.return_value = True

    def test_cancel_cancellable_task(self):
        """取消可取消任务"""
        task = AppTask(
            id="task1",
            name="Test",
            status=TaskStatus.RUNNING,
            cancellable=True,
        )
        task._cancel_event = asyncio.Event()
        self.manager._tasks = {"task1": task}

        self.manager._cancel_task_impl("task1")

        self.assertEqual(task.status, TaskStatus.CANCELLED)

    def test_cancel_non_cancellable_task(self):
        """不可取消任务"""
        task = AppTask(
            id="task1",
            name="Test",
            status=TaskStatus.RUNNING,
            cancellable=False,
        )
        self.manager._tasks = {"task1": task}

        self.manager._cancel_task_impl("task1")

        self.assertEqual(task.status, TaskStatus.RUNNING)

    def test_cancel_nonexistent_task(self):
        """取消不存在的任务"""
        self.manager._cancel_task_impl("nonexistent")

    def test_cancel_finished_task(self):
        """取消已完成任务"""
        task = AppTask(
            id="task1",
            name="Test",
            status=TaskStatus.COMPLETED,
            cancellable=True,
        )
        self.manager._tasks = {"task1": task}

        self.manager._cancel_task_impl("task1")

        self.assertEqual(task.status, TaskStatus.COMPLETED)


class TestTaskManagerClearFinished(unittest.TestCase):
    """测试任务管理器清理功能"""

    def setUp(self):
        TaskManager._instance = None
        self.manager = TaskManager()
        self.manager._loop = MagicMock()
        self.manager._loop.is_running.return_value = True

    def test_clear_finished_tasks(self):
        """清理已完成任务"""
        task1 = AppTask(id="task1", name="Running", status=TaskStatus.RUNNING)
        task2 = AppTask(id="task2", name="Completed", status=TaskStatus.COMPLETED)
        task3 = AppTask(id="task3", name="Failed", status=TaskStatus.FAILED)
        self.manager._tasks = {"task1": task1, "task2": task2, "task3": task3}

        self.manager._clear_finished_impl()

        self.assertIn("task1", self.manager._tasks)
        self.assertNotIn("task2", self.manager._tasks)
        self.assertNotIn("task3", self.manager._tasks)


class TestTaskManagerAutoEvict(unittest.TestCase):
    """测试任务管理器自动清理"""

    def setUp(self):
        TaskManager._instance = None
        self.manager = TaskManager()

    def test_auto_evict_old_tasks(self):
        """自动清理旧任务"""
        for i in range(250):
            task = AppTask(
                id=f"task{i}",
                name=f"Task {i}",
                status=TaskStatus.COMPLETED,
                completed_at=datetime.datetime.now(),
            )
            self.manager._tasks[f"task{i}"] = task

        self.manager._auto_evict_old()

        self.assertLessEqual(len(self.manager._tasks), 200)


class TestTaskManagerSafeDatetime(unittest.TestCase):
    """测试安全日期时间解析"""

    def test_safe_dt_none(self):
        """None 输入"""
        result = TaskManager._safe_dt(None)
        self.assertIsNone(result)

    def test_safe_dt_nan(self):
        """NaN 输入"""
        import numpy as np

        result = TaskManager._safe_dt(np.nan)
        self.assertIsNone(result)

    def test_safe_dt_valid_string(self):
        """有效字符串"""
        result = TaskManager._safe_dt("2024-01-15 10:30:00")

        self.assertIsNotNone(result)
        self.assertEqual(result.year, 2024)  # type: ignore
        self.assertEqual(result.month, 1)  # type: ignore
        self.assertEqual(result.day, 15)  # type: ignore

    def test_safe_dt_invalid_string(self):
        """无效字符串"""
        result = TaskManager._safe_dt("invalid date")
        self.assertIsNone(result)


class TestTaskManagerSubmitTask(unittest.TestCase):
    """测试任务提交"""

    def setUp(self):
        TaskManager._instance = None
        self.manager = TaskManager()
        self.manager._loop = MagicMock()
        self.manager._loop.is_running.return_value = True

    def test_submit_task_returns_id(self):
        """提交任务返回 ID"""

        async def dummy_coro(task_id):
            return "done"

        task_id = self.manager.submit_task(
            name="Test Task",
            task_type="Test",
            coroutine_factory=dummy_coro,
        )

        self.assertIsNotNone(task_id)

    def test_submit_duplicate_task(self):
        """提交重复任务 - 需要已有任务在运行中"""
        task1 = AppTask(
            id="existing_task",
            name="Existing Task",
            status=TaskStatus.RUNNING,
            unique_key="unique_key_1",
        )
        self.manager._tasks = {"existing_task": task1}

        async def dummy_coro(task_id):
            return "done"

        task_id2 = self.manager.submit_task(
            name="Task 2",
            task_type="Test",
            coroutine_factory=dummy_coro,
            unique_key="unique_key_1",
        )

        self.assertIsNone(task_id2)


class TestTaskManagerPersistenceFlush(unittest.TestCase):
    """测试持久化 flush 行为"""

    def setUp(self):
        TaskManager._instance = None
        self.manager = TaskManager()
        self.manager._db_ready = True

    def test_flush_persistence_waits_until_no_pending(self):
        """flush 会等待挂起写入完成"""

        async def run():
            with self.manager._persist_counter_lock:
                self.manager._persist_pending_count = 1

            async def complete_later():
                await asyncio.sleep(0.02)
                with self.manager._persist_counter_lock:
                    self.manager._persist_pending_count = 0

            asyncio.create_task(complete_later())
            await self.manager.flush_persistence(timeout_s=0.5)

            with self.manager._persist_counter_lock:
                self.assertEqual(self.manager._persist_pending_count, 0)

        asyncio.run(run())

    def test_flush_persistence_timeout(self):
        """flush 超时会抛出 TimeoutError"""

        async def run():
            with self.manager._persist_counter_lock:
                self.manager._persist_pending_count = 1
            with self.assertRaises(TimeoutError):
                await self.manager.flush_persistence(timeout_s=0.01)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
