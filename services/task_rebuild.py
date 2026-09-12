"""业务任务的可重建工厂注册区 (LIFE-01, FR-UX-006)。

崩溃后重试（INTERRUPTED → retry）的核心：不序列化 factory 闭包本身，而是序列化其
注册键（``factory_key``），app 重启后按键从注册表查回可独立运行的工厂再执行。

本模块是内置可重建任务工厂的唯一注册点：模块 import 时调用
``TaskManager.register_retryable_factory`` 自注册。``TaskManager.init_db`` 在反序列化
历史任务前惰性 import 本模块（side-effect 触发注册），确保 factory 早于回填可用。

分层说明（R1）：本模块属于 services 层，可 import data 层（services → data 合法）；
不放 data 层以避开 data → services 的禁止方向。
"""

from __future__ import annotations

import asyncio
import logging

from core.i18n import Message
from data.sync.errors import InitSyncError
from services.task_manager import TaskManager

logger = logging.getLogger(__name__)

# key 同时持久化到 task_history.factory_key，须保持稳定（历史数据不能因改名失效）
INIT_HISTORICAL_SYNC_KEY = "init_historical_sync"


async def _rebuild_init_historical_sync(task_id: str, **kwargs) -> Message:
    """重放「历史数据初始化同步」任务的重建工厂（崩溃恢复专用，无 UI VM 依赖）。

    仅由 TaskManager 在重试 INTERRUPTED/FAILED 任务时调用。与 DataSourceViewModel 内
    ``_run_initial_sync`` 闭包职责对等，但不依赖 VM 的 wizard 状态：进度经
    ``TaskManager.update_progress`` 上报到任务中心，保证用户在任务中心能看到恢复中
    任务的进度（解决"纯数据重建任务无 UI 进度反馈"的感知问题）。

    注册工厂的 kwargs 契约：须 JSON 可序列化（持久化到 ``retry_kwargs`` 列后能在
    下次启动反序列化）；同步层基于已缓存日期的跳过逻辑保证重推即自然续传。
    """
    tm = TaskManager()
    # DataProcessor 为注册单例，惰性构造；后台任务上下文不阻塞 UI 线程
    from data.data_processor import DataProcessor

    dp = DataProcessor()

    def _progress(current: int | float, total: int, message) -> None:
        if total and total > 0 and current is not None:
            ratio = min(1.0, current / total)
            if not tm.update_progress(task_id, ratio, message):
                # 任务已被取消/终止，尽早抛出 CancelledError 配合优雅停机 (R2)
                raise asyncio.CancelledError("task cancelled (update_progress returned False)")

    report = await dp.initialize_system(progress_callback=_progress, **kwargs)
    if report is None:
        # 抛异常使 task 进入 FAILED（可重试），而非返回成功造成"已完成"假象
        raise InitSyncError(Message("ds_init_fail_generic"))
    return Message("sys_init_success")


# 模块 import 即注册（幂等，最后一次注册生效）
TaskManager.register_retryable_factory(INIT_HISTORICAL_SYNC_KEY, _rebuild_init_historical_sync)
