"""services/task_rebuild.py 单测 (LIFE-01, FR-UX-006)。

覆盖 `_rebuild_init_historical_sync` 重建工厂的完整逻辑路径：
- 正常重放（数据处理器返回报告 → 返回成功 Message）
- 报告为 None（同步失败 → InitSyncError，任务进入可重试 FAILED 状态）
- 进度上报被拒（update_progress 返回 False → 抛 CancelledError 配合优雅停机 R2）
- 进度非法值（total=0 / current=None）跳过上报，不误抛取消
并验证模块 import 时的工厂自注册副作用（即使单例已复位，reload 后仍能注册）。
"""

from __future__ import annotations

import asyncio
import importlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.i18n import Message
from data import data_processor
from data.sync.errors import InitSyncError
from services import task_rebuild  # noqa: F401  # 触发模块自注册副作用
from services.task_manager import TaskManager

SAMPLE_KWARGS = {"quick": False, "days": 250}


def _install_fake_processor(monkeypatch: pytest.MonkeyPatch, initialize_impl):
    """以实时可控的初始化实现替换 data_processor.DataProcessor 单例构造点。

    ``_rebuild_init_historical_sync`` 在函数体内 ``from data.data_processor import
    DataProcessor`` 惰性取类并构造，因此替换模块属性即可拦截其构造与初始化调用。
    """
    fake = MagicMock()
    fake.initialize_system = AsyncMock(side_effect=initialize_impl)
    monkeypatch.setattr(data_processor, "DataProcessor", lambda: fake)
    return fake


async def test_module_import_registers_factory():
    """模块 import 即注册 INIT_HISTORICAL_SYNC_KEY（reload 保证复位后仍可断言）。"""
    importlib.reload(task_rebuild)
    factory = TaskManager._RETRYABLE_FACTORIES.get(task_rebuild.INIT_HISTORICAL_SYNC_KEY)
    assert callable(factory)
    assert factory is task_rebuild._rebuild_init_historical_sync


async def test_rebuild_success_reports_and_returns_success(monkeypatch: pytest.MonkeyPatch):
    """正常路径：进度上报被接受，返回 sys_init_success。

    同时覆盖 _progress 的非法值短路（total=0 / current=None 不上报不抛错）。
    """

    def initialize_impl(progress_callback=None, **kwargs):
        assert progress_callback is not None
        progress_callback(5, 10, Message("init_sync_market_snapshot"))  # 正常占比
        progress_callback(1, 0, Message("irrelevant"))  # total=0 → 跳过
        progress_callback(None, 10, Message("irrelevant"))  # current=None → 跳过
        assert kwargs == SAMPLE_KWARGS
        return {"ok": True}

    fake = _install_fake_processor(monkeypatch, initialize_impl)
    # 真实 TaskManager 对不存在的任务 update_progress 返回 False；此处模拟"任务仍 RUNNING，
    # 进度被接受"的语义，仅走正常上报路径。
    monkeypatch.setattr(TaskManager, "update_progress", lambda self, *a, **k: True)

    result = await task_rebuild._rebuild_init_historical_sync("task-1", **SAMPLE_KWARGS)

    assert isinstance(result, Message)
    assert result.key == "sys_init_success"
    fake.initialize_system.assert_awaited_once()


async def test_rebuild_none_report_raises_init_sync_error(monkeypatch: pytest.MonkeyPatch):
    """同步无返回报告视为失败：抛 InitSyncError 使任务进入可重试 FAILED。"""

    def initialize_impl(progress_callback=None, **kwargs):
        return None

    _install_fake_processor(monkeypatch, initialize_impl)

    with pytest.raises(InitSyncError) as exc_info:
        await task_rebuild._rebuild_init_historical_sync("task-1", **SAMPLE_KWARGS)

    assert exc_info.value.args[0].key == "ds_init_fail_generic"


async def test_rebuild_progress_rejected_raises_cancelled(monkeypatch: pytest.MonkeyPatch):
    """进度上报被 TaskManager 拒绝（任务已取消/终止）→ 抛 CancelledError (R2)。"""

    def initialize_impl(progress_callback=None, **kwargs):
        assert progress_callback is not None
        progress_callback(5, 10, Message("init_sync_market_snapshot"))
        return {"ok": True}

    _install_fake_processor(monkeypatch, initialize_impl)
    monkeypatch.setattr(TaskManager, "update_progress", lambda self, *a, **k: False)

    with pytest.raises(asyncio.CancelledError) as exc_info:
        await task_rebuild._rebuild_init_historical_sync("task-1", **SAMPLE_KWARGS)

    assert "task cancelled" in str(exc_info.value)
