"""AutomationSettingsViewModel 单元测试 (Task 5.2 TDD Red).

测试 VM state/commands, 不依赖 Flet 渲染。
覆盖：
- frozen state 不可变 (AutomationSettingsState)
- 计划任务保存 (save_auto_update_enabled / save_auto_update_time)
- AI 概念任务保存 (save_ai_concept_enabled / save_ai_concept_time / save_ai_concept_engine)
- 新闻提醒保存 (save_news_enabled / save_news_interval)
- 保存成功/失败/取消/重复提交
- 构造注入 ConfigHandler/ThreadPoolManager
- 同步阻塞操作走 ThreadPoolManager (R16)
- R2 CancelledError 显式 raise
"""

import asyncio
from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ui.viewmodels.automation_settings_view_model import (
    AutomationSettingsState,
    AutomationSettingsViewModel,
)
from utils.thread_pool import TaskType

pytestmark = pytest.mark.unit


# --- Fixtures ---


@pytest.fixture
def mock_config_handler():
    """Mock ConfigHandler 模块级 patch。"""
    with patch("ui.viewmodels.automation_settings_view_model.ConfigHandler") as m:
        m.is_auto_update_enabled.return_value = False
        m.get_auto_update_time.return_value = "16:30"
        m.is_ai_concept_schedule_enabled.return_value = False
        m.get_ai_concept_schedule_time.return_value = "20:00"
        m.get_ai_concept_search_engine.return_value = "search_std"
        m.get_config.side_effect = lambda key, default=None: {
            "enable_news_alerts": True,
            "news_poll_interval": 60,
        }.get(key, default)
        m.save_config.return_value = True
        m.set_ai_concept_schedule_enabled.return_value = True
        m.set_ai_concept_schedule_time.return_value = True
        m.set_ai_concept_search_engine.return_value = True
        yield m


@pytest.fixture
def mock_thread_pool():
    """Mock ThreadPoolManager.run_async 为同步 passthrough。"""

    async def _passthrough(task_type, func, *args, **kwargs):
        return func(*args, **kwargs)

    mock_tpm = MagicMock()
    mock_tpm.run_async = AsyncMock(side_effect=_passthrough)
    with patch(
        "ui.viewmodels.automation_settings_view_model.ThreadPoolManager",
        return_value=mock_tpm,
    ):
        yield mock_tpm


def _make_vm(mock_config_handler) -> AutomationSettingsViewModel:
    return AutomationSettingsViewModel()


# --- State immutability ---


class TestStateImmutability:
    def test_state_is_frozen(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        with pytest.raises(FrozenInstanceError):
            vm.state.auto_enabled = True  # type: ignore[misc]

    def test_state_default_values(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        assert vm.state.auto_enabled is False
        assert vm.state.auto_time == "16:30"
        assert vm.state.ai_enabled is False
        assert vm.state.ai_time == "20:00"
        assert vm.state.ai_engine == "search_std"
        assert vm.state.news_enabled is True
        assert vm.state.news_interval == "60"
        assert vm.state.is_saving is False


# --- Subscribe / notify ---


class TestSubscribeNotify:
    def test_subscribe_receives_state_changes(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        received: list[AutomationSettingsState] = []
        vm.subscribe(lambda s: received.append(s))
        vm.set_auto_enabled(True)
        assert len(received) == 1
        assert received[0].auto_enabled is True

    def test_dispose_clears_subscribers(self, mock_config_handler):
        vm = _make_vm(mock_config_handler)
        received: list[AutomationSettingsState] = []
        vm.subscribe(lambda s: received.append(s))
        vm.dispose()
        vm.set_auto_enabled(True)
        assert len(received) == 0


# --- save_auto_update_enabled ---


class TestSaveAutoUpdateEnabled:
    @pytest.mark.asyncio
    async def test_save_success(self, mock_config_handler, mock_thread_pool):
        vm = _make_vm(mock_config_handler)
        result = await vm.save_auto_update_enabled(True)
        assert result is True
        mock_config_handler.save_config.assert_called_once_with({"auto_update_enabled": True})

    @pytest.mark.asyncio
    async def test_save_failure_returns_false(self, mock_config_handler, mock_thread_pool):
        mock_config_handler.save_config.return_value = False
        vm = _make_vm(mock_config_handler)
        result = await vm.save_auto_update_enabled(True)
        assert result is False

    @pytest.mark.asyncio
    async def test_save_io_exception_returns_false(self, mock_config_handler, mock_thread_pool):
        mock_thread_pool.run_async = AsyncMock(side_effect=RuntimeError("disk full"))
        vm = _make_vm(mock_config_handler)
        result = await vm.save_auto_update_enabled(True)
        assert result is False

    @pytest.mark.asyncio
    async def test_save_cancelled_error_propagates(self, mock_config_handler, mock_thread_pool):
        mock_thread_pool.run_async = AsyncMock(side_effect=asyncio.CancelledError())
        vm = _make_vm(mock_config_handler)
        with pytest.raises(asyncio.CancelledError):
            await vm.save_auto_update_enabled(True)


# --- save_auto_update_time ---


class TestSaveAutoUpdateTime:
    @pytest.mark.asyncio
    async def test_save_success(self, mock_config_handler, mock_thread_pool):
        vm = _make_vm(mock_config_handler)
        result = await vm.save_auto_update_time("18:00")
        assert result is True
        mock_config_handler.save_config.assert_called_once_with({"auto_update_time": "18:00"})


# --- save_ai_concept_enabled ---


class TestSaveAiConceptEnabled:
    @pytest.mark.asyncio
    async def test_save_success(self, mock_config_handler, mock_thread_pool):
        vm = _make_vm(mock_config_handler)
        result = await vm.save_ai_concept_enabled(True)
        assert result is True
        mock_config_handler.set_ai_concept_schedule_enabled.assert_called_once_with(True)

    @pytest.mark.asyncio
    async def test_save_failure_returns_false(self, mock_config_handler, mock_thread_pool):
        mock_config_handler.set_ai_concept_schedule_enabled.return_value = False
        vm = _make_vm(mock_config_handler)
        result = await vm.save_ai_concept_enabled(True)
        assert result is False


# --- save_ai_concept_time ---


class TestSaveAiConceptTime:
    @pytest.mark.asyncio
    async def test_save_success(self, mock_config_handler, mock_thread_pool):
        vm = _make_vm(mock_config_handler)
        result = await vm.save_ai_concept_time("17:00")
        assert result is True
        mock_config_handler.set_ai_concept_schedule_time.assert_called_once_with("17:00")


# --- save_ai_concept_engine ---


class TestSaveAiConceptEngine:
    @pytest.mark.asyncio
    async def test_save_success(self, mock_config_handler, mock_thread_pool):
        vm = _make_vm(mock_config_handler)
        result = await vm.save_ai_concept_engine("search_pro")
        assert result is True
        mock_config_handler.set_ai_concept_search_engine.assert_called_once_with("search_pro")


# --- save_news_enabled ---


class TestSaveNewsEnabled:
    @pytest.mark.asyncio
    async def test_save_success(self, mock_config_handler, mock_thread_pool):
        vm = _make_vm(mock_config_handler)
        result = await vm.save_news_enabled(False)
        assert result is True
        mock_config_handler.save_config.assert_called_once_with({"enable_news_alerts": False})

    @pytest.mark.asyncio
    async def test_save_failure_returns_false(self, mock_config_handler, mock_thread_pool):
        mock_config_handler.save_config.return_value = False
        vm = _make_vm(mock_config_handler)
        result = await vm.save_news_enabled(True)
        assert result is False


# --- save_news_interval ---


class TestSaveNewsInterval:
    @pytest.mark.asyncio
    async def test_save_success(self, mock_config_handler, mock_thread_pool):
        vm = _make_vm(mock_config_handler)
        result = await vm.save_news_interval("120")
        assert result is True
        mock_config_handler.save_config.assert_called_once_with({"news_poll_interval": 120})

    @pytest.mark.asyncio
    async def test_save_invalid_format_returns_false(self, mock_config_handler, mock_thread_pool):
        vm = _make_vm(mock_config_handler)
        result = await vm.save_news_interval("abc")
        assert result is False
        mock_config_handler.save_config.assert_not_called()


# --- ThreadPoolManager 调用契约 (R16) ---


class TestThreadPoolOffloadContract:
    @pytest.mark.asyncio
    async def test_save_auto_update_enabled_uses_thread_pool(self, mock_config_handler, mock_thread_pool):
        vm = _make_vm(mock_config_handler)
        await vm.save_auto_update_enabled(True)
        assert mock_thread_pool.run_async.call_count == 1
        args, _ = mock_thread_pool.run_async.call_args
        assert args[0] is TaskType.IO
