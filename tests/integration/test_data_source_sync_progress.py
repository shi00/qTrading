"""P1-5 集成测试: DataSourceViewModel × 真实 TaskManager 进度反馈闭环.

与单测 (mock TaskManager) 互补: 本文件用真实 TaskManager 单例验证
submit → 进度上报 → 终态恢复 的完整闭环:

- daily_sync 完成路径: progress_callback 上报 → VM state.progress 更新 →
  任务 COMPLETED → handle_task_update/finally 恢复 is_syncing/active_key/progress
- daily_sync 取消路径: cancel_active_task → TaskManager CANCELLED →
  CancelledError 传播 (R2) → cancel snack + state 恢复
- cache_clear 完成路径: cancellable=False + 无进度回调 (indeterminate) →
  COMPLETED 后 state 恢复 + ds_cache_cleared snack

无需 DB (no_db): TaskManager._db_ready=False 时 _persist_task 为 no-op;
DataProcessor/CacheManager/AIService 均以 spec mock 注入, 不触发真实 IO/单例.
"""

# pyright: reportArgumentType=false, reportAttributeAccessIssue=false, reportOptionalMemberAccess=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 参数类型不兼容（替身类/Optional/dict 替代）, 动态属性访问（mock/stub/monkey-patch）, Optional 成员访问（mock 返回 None）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from data.cache.cache_manager import CacheManager
from data.data_processor import DataProcessor
from services.ai_service import AIService
from services.task_manager import TaskManager, TaskStatus
from ui.viewmodels import Message
from ui.viewmodels.data_source_view_model import DataSourceViewModel

pytestmark = [pytest.mark.integration, pytest.mark.no_db]

_WAIT_TIMEOUT_S = 5.0
_POLL_INTERVAL_S = 0.02


async def _wait_for(predicate, timeout: float = _WAIT_TIMEOUT_S) -> None:
    """轮询 predicate 直到返回 True, 超时抛 TimeoutError。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(_POLL_INTERVAL_S)
    raise TimeoutError(f"_wait_for 超时 ({timeout}s): predicate 未满足")


@pytest.fixture
def task_manager():
    """真实 TaskManager 单例 (隔离), patch ConfigHandler 避免真实配置读取."""
    TaskManager._reset_singleton()
    with (
        patch("services.task_manager.ConfigHandler") as mock_ch,
        patch("services.task_manager.ThreadPoolManager"),
    ):
        mock_ch.get_max_concurrent_tasks.return_value = 2
        yield TaskManager()
    TaskManager._reset_singleton()


@pytest.fixture
def mock_processor():
    instance = MagicMock(spec=DataProcessor)
    instance.run_daily_update = AsyncMock()
    instance.run_ai_concept_tagging = AsyncMock()
    instance.initialize_system = AsyncMock(return_value={"success": True})
    instance.request_cancel = AsyncMock()
    instance.is_cancelled = MagicMock(return_value=False)
    return instance


@pytest.fixture
def mock_cache():
    instance = MagicMock(spec=CacheManager)
    instance.clear_all_cache = AsyncMock()
    return instance


@pytest.fixture
def mock_ai_service():
    instance = MagicMock(spec=AIService)
    instance.is_cloud_available = MagicMock(return_value=True)
    return instance


@pytest.fixture
def vm(task_manager, mock_processor, mock_cache, mock_ai_service):
    """VM 构造时订阅真实 TaskManager (singleton 已由 fixture 初始化)."""
    instance = DataSourceViewModel(
        processor=mock_processor,
        cache=mock_cache,
        ai_service=mock_ai_service,
    )
    yield instance
    instance.dispose()


@pytest.fixture
def snapshots(vm):
    """收集 VM state 快照."""
    collected: list = []
    vm.subscribe(collected.append)
    return collected


class TestDailySyncProgressIntegration:
    """P1-5: daily_sync 进度上报 → VM state → 终态恢复 闭环。"""

    @pytest.mark.asyncio
    async def test_progress_updates_vm_state_then_recovers_on_completion(
        self, task_manager, mock_processor, vm, snapshots
    ):
        """完成路径: progress_callback 上报 0.5/1.0 → state.progress 更新 →
        COMPLETED 后 is_syncing/active_key/progress 恢复 + 成功 snack."""
        task_manager._loop = asyncio.get_running_loop()

        async def _fake_daily_update(progress_callback=None):
            assert progress_callback is not None
            progress_callback(1, 2, "sync_step_1")
            await asyncio.sleep(0)
            progress_callback(2, 2, "sync_step_2")

        mock_processor.run_daily_update.side_effect = _fake_daily_update

        vm.execute_full_daily_sync()
        assert vm.state.is_syncing is True
        assert vm.state.active_key == "daily_sync"

        # 等待任务完成 → state 恢复
        await _wait_for(lambda: vm.state.is_syncing is False)

        # 任务在 TaskManager 中达到 COMPLETED
        task_id = vm._active_task_ids.get("daily_sync")  # 恢复后已清空, 从 TM 历史找
        assert task_id is None  # handle_task_update 终态后清空 _active_task_ids
        completed = [t for t in task_manager.get_all_tasks() if t.unique_key == "daily_sync"]
        assert len(completed) == 1
        assert completed[0].status == TaskStatus.COMPLETED

        # 进度曾上报到 VM state (快照捕获 0.5 与 1.0)
        progress_values = [s.progress for s in snapshots]
        assert 0.5 in progress_values
        assert 1.0 in progress_values
        # 进度消息透传 (Message 包装)
        assert any(s.progress_message == Message("sync_step_1") for s in snapshots)

        # 终态恢复: active_key=None, progress 归零 (P1-5 _set_sync_busy 重置语义)
        assert vm.state.active_key is None
        assert vm.state.progress == 0.0
        assert vm.state.progress_message is None

        # 成功 snack 已发射
        assert vm.state.snack is not None
        assert vm.state.snack.message == Message("snack_full_sync_done_simple")
        assert vm.state.snack.color_name == "success"

    @pytest.mark.asyncio
    async def test_cancel_active_task_cancels_and_recovers(self, task_manager, mock_processor, vm, snapshots):
        """取消路径: cancel_active_task → TaskManager CANCELLED →
        CancelledError 传播 (R2) → cancel snack + state 恢复."""
        task_manager._loop = asyncio.get_running_loop()
        started = asyncio.Event()

        async def _blocking_daily_update(progress_callback=None):
            started.set()
            await asyncio.sleep(60)  # 阻塞直到被取消

        mock_processor.run_daily_update.side_effect = _blocking_daily_update

        vm.execute_full_daily_sync()
        assert vm.state.active_key == "daily_sync"

        # 等待任务进入 RUNNING (协程已开始执行)
        await _wait_for(started.is_set)

        vm.cancel_active_task()

        # 等待取消传播完成 → state 恢复
        await _wait_for(lambda: vm.state.is_syncing is False)

        cancelled = [t for t in task_manager.get_all_tasks() if t.unique_key == "daily_sync"]
        assert len(cancelled) == 1
        assert cancelled[0].status == TaskStatus.CANCELLED

        # 取消 snack 已发射 (R2: CancelledError 在 _daily_logic 中显式 raise)
        assert vm.state.snack is not None
        assert vm.state.snack.message == Message("settings_msg_sync_cancelled")
        assert vm.state.snack.color_name == "warning"

        # 终态恢复
        assert vm.state.active_key is None
        assert vm.state.progress == 0.0
        assert vm._active_task_ids == {}

    @pytest.mark.asyncio
    async def test_cancel_active_task_delegates_to_task_manager(self, task_manager, mock_processor, vm):
        """cancel_active_task 委托 TaskManager.cancel_task (task_id 来自 _active_task_ids)."""
        task_manager._loop = asyncio.get_running_loop()
        started = asyncio.Event()

        async def _blocking_daily_update(progress_callback=None):
            started.set()
            await asyncio.sleep(60)

        mock_processor.run_daily_update.side_effect = _blocking_daily_update

        vm.execute_full_daily_sync()
        await _wait_for(started.is_set)

        task_id = vm._active_task_ids["daily_sync"]
        with patch.object(task_manager, "cancel_task", wraps=task_manager.cancel_task) as spy:
            vm.cancel_active_task()
            spy.assert_called_once_with(task_id)

        await _wait_for(lambda: vm.state.is_syncing is False)


class TestClearCacheProgressIntegration:
    """P1-5: cache_clear (cancellable=False, 无进度回调) 完成路径。"""

    @pytest.mark.asyncio
    async def test_cache_clear_completes_and_recovers(self, task_manager, mock_cache, vm):
        """cache_clear 无进度回调 → COMPLETED 后 state 恢复 + ds_cache_cleared snack."""
        task_manager._loop = asyncio.get_running_loop()

        vm.execute_clear_cache()
        assert vm.state.is_syncing is True
        assert vm.state.active_key == "cache_clear"

        await _wait_for(lambda: vm.state.is_syncing is False)

        completed = [t for t in task_manager.get_all_tasks() if t.unique_key == "cache_clear"]
        assert len(completed) == 1
        assert completed[0].status == TaskStatus.COMPLETED
        # cancellable=False: View 不显示取消按钮的语义来源
        assert completed[0].cancellable is False

        mock_cache.clear_all_cache.assert_awaited_once()
        assert vm.state.snack is not None
        assert vm.state.snack.message == Message("ds_cache_cleared")
        assert vm.state.snack.color_name == "success"

        # 终态恢复 (无进度回调, progress 保持 0.0)
        assert vm.state.active_key is None
        assert vm.state.progress == 0.0
        assert vm._active_task_ids == {}
