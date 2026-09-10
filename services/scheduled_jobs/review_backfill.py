"""T+5 延迟回填定时任务（D2-4）。

SchedulerService 每个交易日固定触发一次，对本任务无需 idempotency 标记——
``ReviewManager.backfill_horizon_returns`` 只回填 ``t5_pct IS NULL`` 的记录，
天然幂等；每日运行是刚需（新预测逐日满足 5 个交易日成熟）。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from data.persistence.review_manager import ReviewManager
from utils.error_classifier import log_classified

if TYPE_CHECKING:
    from utils.scheduler_service import SchedulerService

logger = logging.getLogger(__name__)


async def _run_review_backfill(svc: SchedulerService) -> str:
    """T+5 延迟回填 job：调用 ReviewManager.backfill_horizon_returns，返回回填条数。"""
    try:
        rm = ReviewManager()
        count = await rm.backfill_horizon_returns()
        logger.info("[Scheduler] T+5 backfill completed: %s records updated.", count)
        return f"T+5 backfilled: {count}"
    except Exception as e:
        log_classified(
            logger,
            e,
            "general",
            "[Scheduler] T+5 backfill job failed (%s): %s",
            exc_info=True,
        )
        raise


def build_review_backfill_job() -> Callable[[SchedulerService], Awaitable[None]]:
    """构造 T+5 回填 job，供 SchedulerService.register_job("review_backfill") 注册。"""

    async def _job(svc: SchedulerService) -> None:
        await _run_review_backfill(svc)

    return _job
