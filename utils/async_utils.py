"""异步工具函数 — 封装 asyncio.gather 的 R2 合规变体。

项目规则 R2 要求 CancelledError 必须传播，但 asyncio.gather(return_exceptions=True)
会将 CancelledError 当作普通返回值吞没。本模块提供两种语义的封装：

1. gather_return_exceptions_propagating_cancel — 业务并发用
   普通异常保留在结果列表中，CancelledError 立即重新抛出。

2. gather_for_shutdown_cleanup — 关机清理用
   CancelledError 视为预期结果（已取消的任务），不重新抛出；
   普通异常记录 warning 日志，不抛出，避免打断清理流程。
"""

import asyncio
import logging
from collections.abc import Awaitable
from typing import Any

logger = logging.getLogger(__name__)


async def gather_return_exceptions_propagating_cancel(*coros: Awaitable[Any]) -> list[Any]:
    """asyncio.gather(return_exceptions=True) 的 R2 合规封装。

    与原生 gather(return_exceptions=True) 行为一致，但会检查返回值中
    是否包含 CancelledError，如果有则立即重新抛出，确保关机信号不被吞没。

    用法:
        results = await gather_return_exceptions_propagating_cancel(task1, task2)
        # results 中不会包含 CancelledError（已被重新抛出）
        # 普通异常仍保留在结果列表中，需调用方检查 isinstance(x, Exception)
    """
    results = await asyncio.gather(*coros, return_exceptions=True)
    for r in results:
        if isinstance(r, asyncio.CancelledError):
            raise r
    return results


async def gather_for_shutdown_cleanup(*coros: Awaitable[Any]) -> list[Any]:
    """关机清理场景专用的 gather 封装。

    CancelledError 不重新抛出（已取消的任务返回 CancelledError 是预期行为），
    普通异常记录 warning 日志但不抛出，避免打断关机清理流程。

    用法:
        results = await gather_for_shutdown_cleanup(task1, task2)
        # results 中可能包含 CancelledError 和普通异常
    """
    results = await asyncio.gather(*coros, return_exceptions=True)
    for r in results:
        if isinstance(r, Exception) and not isinstance(r, asyncio.CancelledError):
            logger.warning("[ShutdownCleanup] Task failed during cleanup: %s", r)
    return results
